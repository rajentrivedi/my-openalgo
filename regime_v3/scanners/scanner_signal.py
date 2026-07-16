from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ScannerSignal:
    timestamp: pd.Timestamp
    symbol: str
    scanner_name: str
    score: float
    rank: int
    direction: str
    confidence: float
    timeframe: str
    features: dict[str, float] = field(default_factory=dict)
    signal_state: str = "ACTIVE"
    raw_score: float = 0.0

    def __post_init__(self) -> None:
        if self.raw_score == 0.0:
            self.raw_score = self.score

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "scanner_name": self.scanner_name,
            "score": self.score,
            "rank": self.rank,
            "direction": self.direction,
            "confidence": self.confidence,
            "timeframe": self.timeframe,
            "features": self.features,
            "signal_state": self.signal_state,
            "raw_score": self.raw_score,
        }


@dataclass
class NormalizedSignal:
    timestamp: pd.Timestamp
    symbol: str
    scanner_name: str
    raw_score: float
    normalized_score: float
    percentile_score: float
    direction: str
    confidence: float
    features: dict

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "scanner_name": self.scanner_name,
            "raw_score": self.raw_score,
            "normalized_score": self.normalized_score,
            "percentile_score": self.percentile_score,
            "direction": self.direction,
            "confidence": self.confidence,
            "features": self.features,
        }


@dataclass
class ScannerConsensus:
    timestamp: pd.Timestamp
    symbol: str
    scanner_count: int
    available_scanners: int
    bullish_count: int
    bearish_count: int
    neutral_count: int
    bullish_ratio: float
    bearish_ratio: float
    consensus: str
    mean_rank: float
    median_rank: float
    best_rank: int
    worst_rank: int
    rank_std: float
    score_mean: float
    score_median: float
    score_std: float
    ensemble_score: float
    diversity_score: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "scanner_count": self.scanner_count,
            "available_scanners": self.available_scanners,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "bullish_ratio": self.bullish_ratio,
            "bearish_ratio": self.bearish_ratio,
            "consensus": self.consensus,
            "mean_rank": self.mean_rank,
            "median_rank": self.median_rank,
            "best_rank": self.best_rank,
            "worst_rank": self.worst_rank,
            "rank_std": self.rank_std,
            "score_mean": self.score_mean,
            "score_median": self.score_median,
            "score_std": self.score_std,
            "ensemble_score": self.ensemble_score,
            "diversity_score": self.diversity_score,
        }


@dataclass
class RankedCandidate:
    timestamp: pd.Timestamp
    symbol: str
    ensemble_score: float
    scanner_count: int
    bullish_scanners: int
    bearish_scanners: int
    scanner_rank_mean: float
    scanner_rank_median: float
    scanner_rank_best: int
    scanner_rank_worst: int
    consensus: str
    regime: str
    direction: str
    llm_rank: int = 0
    llm_score: float = 0.0
    llm_selected: bool = False
    llm_confidence: float = 0.0
    llm_reason_codes: list[str] = field(default_factory=list)
    selection_method: str = ""
    forward_return_1: float = 0.0
    forward_return_3: float = 0.0
    forward_return_6: float = 0.0
    forward_return_12: float = 0.0
    forward_return_24: float = 0.0
    nifty_forward_return: float = 0.0
    excess_return_1: float = 0.0
    excess_return_3: float = 0.0
    excess_return_6: float = 0.0
    excess_return_12: float = 0.0
    excess_return_24: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "ensemble_score": self.ensemble_score,
            "scanner_count": self.scanner_count,
            "bullish_scanners": self.bullish_scanners,
            "bearish_scanners": self.bearish_scanners,
            "scanner_rank_mean": self.scanner_rank_mean,
            "scanner_rank_median": self.scanner_rank_median,
            "scanner_rank_best": self.scanner_rank_best,
            "scanner_rank_worst": self.scanner_rank_worst,
            "consensus": self.consensus,
            "regime": self.regime,
            "direction": self.direction,
            "llm_rank": self.llm_rank,
            "llm_score": self.llm_score,
            "llm_selected": self.llm_selected,
            "llm_confidence": self.llm_confidence,
            "llm_reason_codes": self.llm_reason_codes,
            "selection_method": self.selection_method,
            "forward_return_1": self.forward_return_1,
            "forward_return_3": self.forward_return_3,
            "forward_return_6": self.forward_return_6,
            "forward_return_12": self.forward_return_12,
            "forward_return_24": self.forward_return_24,
            "nifty_forward_return": self.nifty_forward_return,
            "excess_return_1": self.excess_return_1,
            "excess_return_3": self.excess_return_3,
            "excess_return_6": self.excess_return_6,
            "excess_return_12": self.excess_return_12,
            "excess_return_24": self.excess_return_24,
        }


EXPERIMENT_REGISTRY: dict[str, dict[str, Any]] = {}


def experiment_registry_entry(
    description: str,
    code_version: str,
    prompt_version: str,
    scanner_versions: dict[str, str],
    regime_version: str,
    parameters: dict[str, Any] | None = None,
    dataset: dict[str, Any] | None = None,
    results: dict[str, Any] | None = None,
    decision: str = "REJECT",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "experiment_id": str(uuid.uuid4()),
        "date": date.today().isoformat(),
        "description": description,
        "code_version": code_version,
        "prompt_version": prompt_version,
        "scanner_versions": scanner_versions,
        "regime_version": regime_version,
        "parameters": parameters or {},
        "dataset": dataset or {},
        "results": results or {},
        "decision": decision,
    }
    EXPERIMENT_REGISTRY[entry["experiment_id"]] = entry
    return entry
