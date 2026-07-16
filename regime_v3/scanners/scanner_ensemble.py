"""
Cross-scanner ensemble — combines normalized signals into consensus + ensemble score.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from .scanner_registry import get_scanner, list_scanners
from .scanner_signal import NormalizedSignal, RankedCandidate, ScannerConsensus


def compute_consensus(
    normalized: dict[str, dict[str, NormalizedSignal]],
    timestamp: pd.Timestamp,
) -> dict[str, ScannerConsensus]:
    """
    Build consensus for every symbol from all scanner normalized signals.

    Args:
        normalized: {symbol: {scanner_name: NormalizedSignal}}
        timestamp: current decision time

    Returns:
        {symbol: ScannerConsensus}
    """
    consensi: dict[str, ScannerConsensus] = {}
    all_scanners = list_scanners()
    n_scanners = len(all_scanners)

    for symbol, scanner_map in normalized.items():
        if not scanner_map:
            continue

        signals = list(scanner_map.values())
        avail = len(signals)

        bullish = sum(1 for s in signals if s.direction == "LONG")
        bearish = sum(1 for s in signals if s.direction == "SHORT")
        neutral = avail - bullish - bearish

        bullish_ratio = bullish / max(avail, 1)
        bearish_ratio = bearish / max(avail, 1)

        # Consensus label
        if avail <= 1:
            consensus_str = "NO_CONSENSUS" if avail == 0 else "SINGLE"
        elif bullish == avail or (bullish > bearish and bullish >= avail * 0.65):
            consensus_str = "STRONG_BULLISH"
        elif bullish > bearish:
            consensus_str = "BULLISH"
        elif bearish == avail or (bearish > bullish and bearish >= avail * 0.65):
            consensus_str = "STRONG_BEARISH"
        elif bearish > bullish:
            consensus_str = "BEARISH"
        else:
            consensus_str = "NO_CONSENSUS"
        if avail == 0:
            consensus_str = "NO_CONSENSUS"

        # Score aggregation
        scores = [s.normalized_score for s in signals]
        percs = [s.percentile_score for s in signals]
        confs = [s.confidence for s in signals]

        # Ensemble score: mean of normalized scores weighted by confidence
        if len(confs) > 0 and sum(confs) > 0:
            ensemble = sum(s * c for s, c in zip(scores, confs, strict=False)) / sum(confs)
        else:
            ensemble = np.mean(scores) if scores else 0.0

        # Direction based consensus score
        directional_scores = []
        for s in signals:
            if s.direction == "LONG":
                directional_scores.append(s.normalized_score)
            elif s.direction == "SHORT":
                directional_scores.append(1.0 - s.normalized_score)

        if directional_scores:
            directional_ensemble = np.mean(directional_scores)
            # Blend: 60% score-based, 40% directional
            ensemble = 0.6 * ensemble + 0.4 * directional_ensemble

        ranks = [s.features.get("_rank", 0) for s in signals if "_rank" in s.features]

        # Diversity: 1 - mean pairwise correlation of percentile scores
        if len(percs) > 1:
            diversity = 1.0 - float(np.std(percs) / max(np.mean(percs), 1e-10))
            diversity = min(max(diversity, 0.0), 1.0)
        else:
            diversity = 1.0

        consensi[symbol] = ScannerConsensus(
            timestamp=timestamp,
            symbol=symbol,
            scanner_count=avail,
            available_scanners=n_scanners,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            bullish_ratio=round(bullish_ratio, 4),
            bearish_ratio=round(bearish_ratio, 4),
            consensus=consensus_str,
            mean_rank=float(np.mean(ranks)) if ranks else 0.0,
            median_rank=float(np.median(ranks)) if ranks else 0.0,
            best_rank=int(min(ranks)) if ranks else 0,
            worst_rank=int(max(ranks)) if ranks else 0,
            rank_std=float(np.std(ranks)) if len(ranks) > 1 else 0.0,
            score_mean=float(np.mean(scores)),
            score_median=float(np.median(scores)),
            score_std=float(np.std(scores)) if len(scores) > 1 else 0.0,
            ensemble_score=round(float(ensemble), 4),
            diversity_score=round(diversity, 4),
        )

    return consensi


def build_ranked_candidates(
    consensi: dict[str, ScannerConsensus],
    regime: str,
    timestamp: pd.Timestamp,
) -> list[RankedCandidate]:
    """
    Convert consensi into sorted ranked candidate list.
    """
    candidates = []
    for symbol, c in consensi.items():
        # Low-scanner-count symbols get a penalty but are still included
        if c.scanner_count < config.MIN_SCANNERS_FOR_VOTE:
            continue  # skip entirely; prevents noise from single-scanner signals

        # Direction from consensus - require at least 2 bullish/bearish vote
        if c.bullish_count > c.bearish_count and c.bullish_count >= 2:
            direction = "LONG"
        elif c.bearish_count > c.bullish_count and c.bearish_count >= 2:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        candidates.append(RankedCandidate(
            timestamp=timestamp,
            symbol=symbol,
            ensemble_score=c.ensemble_score,
            scanner_count=c.scanner_count,
            bullish_scanners=c.bullish_count,
            bearish_scanners=c.bearish_count,
            scanner_rank_mean=c.mean_rank,
            scanner_rank_median=c.median_rank,
            scanner_rank_best=c.best_rank,
            scanner_rank_worst=c.worst_rank,
            consensus=c.consensus,
            regime=regime,
            direction=direction,
        ))

    # Sort by ensemble score descending
    candidates.sort(key=lambda c: c.ensemble_score, reverse=True)
    return candidates


def rank_candidates_deterministic(
    candidates: list[RankedCandidate],
    top_n: int = 5,
) -> list[RankedCandidate]:
    """
    Select top-N by deterministic ensemble ranking.
    """
    selected = candidates[:top_n]
    for i, c in enumerate(selected):
        c.llm_rank = i + 1
        c.llm_score = c.ensemble_score * 100
        c.llm_selected = True
        c.llm_confidence = 0.5
        c.selection_method = config.SELECT_ENSEMBLE_TOP
    return selected
