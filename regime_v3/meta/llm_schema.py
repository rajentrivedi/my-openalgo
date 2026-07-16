"""
LLM input/output schemas for structured meta-ranking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMInputCandidate:
    symbol: str
    ensemble_score: float
    scanner_count: int
    bullish_scanners: int
    bearish_scanners: int
    scanner_rank_mean: float
    scanner_rank_median: float
    mtf_score: float | None = None
    daily_score: float | None = None
    one_hour_score: float | None = None
    fifteen_min_score: float | None = None
    rvol: float | None = None
    adx: float | None = None
    relative_strength_20: float | None = None
    ret_5: float | None = None
    ret_20: float | None = None
    atr_pct: float | None = None
    conflict: bool | None = None
    bias: str | None = None
    consensus: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class LLMInput:
    timestamp: str
    market_regime: str
    candidate_count: int
    candidates: list[LLMInputCandidate]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "market_regime": self.market_regime,
            "candidate_count": self.candidate_count,
            "candidates": [c.to_dict() for c in self.candidates],
        }


@dataclass
class LLMOutputStock:
    symbol: str
    rank: int
    score: float
    direction: str
    confidence: float
    reason_codes: list[str] = field(default_factory=list)

    def validate(self, candidate_symbols: set[str]) -> list[str]:
        errors = []
        if self.symbol not in candidate_symbols:
            errors.append(f"symbol '{self.symbol}' not in candidate universe")
        if self.rank < 1:
            errors.append(f"invalid rank {self.rank}")
        if not (0 <= self.score <= 100):
            errors.append(f"score {self.score} out of 0-100 range")
        if self.direction not in ("LONG", "SHORT", "NEUTRAL"):
            errors.append(f"invalid direction '{self.direction}'")
        if not (0 <= self.confidence <= 1.0):
            errors.append(f"confidence {self.confidence} out of 0-1 range")
        return errors


@dataclass
class LLMOutput:
    regime: str
    ranked_stocks: list[LLMOutputStock]

    def validate(self, candidate_symbols: set[str]) -> list[str]:
        errors = []
        if self.regime not in ("BULL", "BEAR", "SIDEWAYS", "TRANSITION"):
            errors.append(f"invalid regime '{self.regime}'")
        seen = set()
        for stock in self.ranked_stocks:
            errors.extend(stock.validate(candidate_symbols))
            if stock.symbol in seen:
                errors.append(f"duplicate symbol '{stock.symbol}'")
            seen.add(stock.symbol)
        ranks = [s.rank for s in self.ranked_stocks]
        if ranks != sorted(ranks):
            errors.append("ranks are not sequential")
        if len(set(ranks)) != len(ranks):
            errors.append("duplicate ranks")
        return errors
