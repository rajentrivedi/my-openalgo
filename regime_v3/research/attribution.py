"""
Attribution analysis — compare selection methods, scanner contributions, IC, and LLM value-add.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..backtest.engine import run_backtest, run_walkforward
from ..meta.deterministic_ranker import (
    compute_rank_similarity,
    consensus_selection,
    random_selection,
)
from ..scanners.scanner_signal import RankedCandidate, ScannerConsensus


def compare_selection_methods(
    candidates: list[RankedCandidate],
    top_n: int = 5,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Compare multiple selection methods on the same candidate set.
    Returns similarity matrix and rankings.
    """
    methods = {}

    # Random (multiple runs)
    methods["random_1"] = {c.symbol for c in random_selection(candidates, top_n, seed=seed)}
    methods["random_2"] = {c.symbol for c in random_selection(candidates, top_n, seed=seed + 1)}

    # Consensus
    cons = consensus_selection(candidates, top_n)
    methods["consensus"] = {c.symbol for c in cons}

    # Ensemble top
    methods["ensemble"] = {c.symbol for c in candidates[:top_n]}

    return {
        "random_1_vs_ensemble": len(methods["random_1"] & methods["ensemble"]),
        "random_2_vs_ensemble": len(methods["random_2"] & methods["ensemble"]),
        "consensus_vs_ensemble": len(methods["consensus"] & methods["ensemble"]),
        "consensus_top5": [c.symbol for c in cons],
        "ensemble_top5": [c.symbol for c in candidates[:top_n]],
    }


def compute_ic(
    scores: np.ndarray,
    forward_returns: np.ndarray,
) -> dict[str, float]:
    """
    Compute information coefficient (Spearman rank correlation) between scores and forward returns.
    """
    mask = ~(np.isnan(scores) | np.isnan(forward_returns))
    if mask.sum() < 10:
        return {"ic": 0.0, "ic_positive_pct": 0.0, "n": int(mask.sum())}

    from scipy.stats import spearmanr
    valid_scores = scores[mask]
    valid_returns = forward_returns[mask]

    rho, pval = spearmanr(valid_scores, valid_returns)
    ic_positive = (valid_returns > 0).mean() * 100

    return {
        "ic": round(float(rho) if not np.isnan(rho) else 0.0, 4),
        "ic_positive_pct": round(float(ic_positive), 2),
        "n": int(mask.sum()),
    }


def scanner_attribution(
    trade_results: pd.DataFrame,
    candidate_log: pd.DataFrame,
) -> pd.DataFrame:
    """
    Measure each scanner's contribution to selected trade outcomes.
    """
    if trade_results.empty or candidate_log.empty:
        return pd.DataFrame()

    rows = []
    for scanner in config.SCANNER_NAMES:
        # Find trades where this scanner's signal was positive for the symbol
        col = f"scanner_{scanner}_score"
        if col in candidate_log.columns:
            sc = candidate_log[candidate_log[col] > 0]
            symbols_with_signal = set(sc["symbol"].unique())
            trades = trade_results[trade_results["symbol"].isin(symbols_with_signal)]
        else:
            trades = trade_results.sample(min(10, len(trade_results))) if not trade_results.empty else pd.DataFrame()

        if trades.empty:
            rows.append({
                "scanner": scanner,
                "trades": 0,
                "net": 0.0,
                "wr": 0.0,
                "mean_excess": 0.0,
            })
            continue

        rows.append({
            "scanner": scanner,
            "trades": len(trades),
            "net": float(trades["net_pnl"].sum()) if "net_pnl" in trades.columns else 0.0,
            "wr": float((trades["net_pnl"] > 0).mean() * 100) if "net_pnl" in trades.columns else 0.0,
            "mean_excess": float(trades.get("excess_return", pd.Series([0])).mean()),
        })

    return pd.DataFrame(rows)


def compute_llm_value_add(
    results_llm: dict,
    results_ensemble: dict,
    results_random: dict,
) -> dict[str, Any]:
    """
    Compare LLM-selected vs ensemble-selected vs random performance.
    """
    llm_metrics = results_llm.get("metrics", {})
    ensemble_metrics = results_ensemble.get("metrics", {})
    random_metrics = results_random.get("metrics", {})

    return {
        "llm_net": llm_metrics.get("net", 0),
        "ensemble_net": ensemble_metrics.get("net", 0),
        "random_net": random_metrics.get("net", 0),
        "llm_vs_ensemble": llm_metrics.get("net", 0) - ensemble_metrics.get("net", 0),
        "llm_vs_random": llm_metrics.get("net", 0) - random_metrics.get("net", 0),
        "llm_wr": llm_metrics.get("wr", 0),
        "ensemble_wr": ensemble_metrics.get("wr", 0),
        "llm_pf": llm_metrics.get("pf", 0),
        "ensemble_pf": ensemble_metrics.get("pf", 0),
    }


def compute_signal_deciles(
    df: pd.DataFrame,
    score_col: str,
    forward_col: str = "excess_return_1",
) -> pd.DataFrame:
    """
    Sort by score_col, divide into deciles, compute forward returns per decile.
    Tests monotonicity of ranking.
    """
    if df.empty or score_col not in df.columns or forward_col not in df.columns:
        return pd.DataFrame()

    valid = df[[score_col, forward_col]].dropna()
    if len(valid) < 20:
        return pd.DataFrame()

    valid["decile"] = pd.qcut(valid[score_col], q=10, labels=[f"D{i}" for i in range(1, 11)])

    result = valid.groupby("decile", observed=False)[forward_col].agg([
        ("mean", "mean"),
        ("median", "median"),
        ("count", "count"),
        ("wr", lambda x: (x > 0).mean() * 100),
    ]).reset_index()

    return result


def forward_return_labeling(
    df: pd.DataFrame,
    horizons: list[int] = None,
) -> pd.DataFrame:
    """Add forward return columns to a DataFrame with close prices."""
    if horizons is None:
        horizons = [1, 3, 6, 12, 24]

    result = df.copy()
    for h in horizons:
        result[f"forward_return_{h}"] = result["close"].shift(-h) / result["close"] - 1
    return result
