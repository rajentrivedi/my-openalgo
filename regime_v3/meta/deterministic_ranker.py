"""
Deterministic ranker — compare multiple selection methods.
Provides ablation baselines: random, consensus-top, scanner-top, ensemble-top.
"""
from __future__ import annotations

import random as rnd

import numpy as np

from .. import config
from ..scanners.scanner_signal import RankedCandidate


def random_selection(
    candidates: list[RankedCandidate],
    top_n: int = 5,
    seed: int | None = None,
) -> list[RankedCandidate]:
    """Randomly select top_n candidates."""
    if seed is not None:
        rnd.seed(seed)
    pool = list(candidates)
    rnd.shuffle(pool)
    selected = pool[:top_n]
    for i, c in enumerate(selected):
        c.llm_rank = i + 1
        c.llm_score = 0.0
        c.llm_selected = True
        c.selection_method = config.SELECT_RANDOM
    return selected


def consensus_selection(
    candidates: list[RankedCandidate],
    top_n: int = 5,
) -> list[RankedCandidate]:
    """Select by highest bullish_ratio (consensus strength), then ensemble_score."""
    sorted_cands = sorted(
        candidates,
        key=lambda c: (
            c.bullish_scanners / max(c.scanner_count, 1),
            c.ensemble_score,
        ),
        reverse=True,
    )
    selected = sorted_cands[:top_n]
    for i, c in enumerate(selected):
        c.llm_rank = i + 1
        c.llm_score = c.bullish_scanners / max(c.scanner_count, 1) * 100
        c.llm_selected = True
        c.selection_method = config.SELECT_CONSENSUS_TOP
    return selected


def scanner_top_selection(
    candidates: list[RankedCandidate],
    scanner_name: str,
    candidate_signals: dict[str, dict[str, float]],  # symbol -> {scanner_name: score}
    top_n: int = 5,
) -> list[RankedCandidate]:
    """Select by a specific scanner's score."""
    scored = []
    for c in candidates:
        scores = candidate_signals.get(c.symbol, {})
        score = scores.get(scanner_name, 0.0)
        scored.append((c, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = [s[0] for s in scored[:top_n]]
    for i, c in enumerate(selected):
        c.llm_rank = i + 1
        c.llm_score = scored[i][1]
        c.llm_selected = True
        c.selection_method = config.SELECT_SCANNER_TOP
    return selected


def compute_rank_similarity(
    method_a: list[RankedCandidate],
    method_b: list[RankedCandidate],
) -> dict:
    """Compare two selection methods."""
    symbols_a = {c.symbol for c in method_a}
    symbols_b = {c.symbol for c in method_b}

    overlap = symbols_a & symbols_b
    jaccard = len(overlap) / max(len(symbols_a | symbols_b), 1)

    # Rank correlation (Spearman-like) for overlapping symbols
    rank_a = {c.symbol: c.llm_rank for c in method_a}
    rank_b = {c.symbol: c.llm_rank for c in method_b}

    common = [s for s in overlap if s in rank_a and s in rank_b]
    if len(common) >= 3:
        from scipy.stats import spearmanr
        rho, _ = spearmanr([rank_a[s] for s in common], [rank_b[s] for s in common])
    else:
        rho = 0.0

    return {
        "overlap_count": len(overlap),
        "jaccard_similarity": round(jaccard, 4),
        "rank_correlation": round(float(rho), 4) if not np.isnan(rho) else 0.0,
    }
