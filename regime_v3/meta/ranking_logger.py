"""
Ranking logger — save all selection decisions and LLM responses for audit.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from .. import config
from ..scanners.scanner_signal import RankedCandidate


def log_llm_input(raw_input: str, timestamp: str) -> str | None:
    """Save raw LLM prompt to file. Returns filename."""
    d = config.make_out_dir()
    fn = f"{d}/llm_input_{timestamp.replace(':', '-')}.json"
    try:
        with open(fn, "w") as f:
            f.write(raw_input)
        return fn
    except Exception:
        return None


def log_llm_output(output: str | None, timestamp: str) -> str | None:
    """Save raw LLM response to file. Returns filename."""
    if output is None:
        return None
    d = config.make_out_dir()
    fn = f"{d}/llm_output_{timestamp.replace(':', '-')}.json"
    try:
        with open(fn, "w") as f:
            f.write(output)
        return fn
    except Exception:
        return None


def log_selection(
    candidates: list[RankedCandidate],
    regime: str,
    timestamp: str,
    out_dir: str | None = None,
) -> str:
    """
    Log candidate universe + selection to CSV.
    Returns filename.
    """
    d = out_dir or config.make_out_dir()
    fn = f"{d}/selection_log.csv"
    import pandas as pd
    rows = [c.to_dict() for c in candidates]
    df = pd.DataFrame(rows)
    write_header = not os.path.exists(fn)
    df.to_csv(fn, mode="a", header=write_header, index=False)
    return fn


def log_llm_rank_stability(
    rankings: list[list[tuple[str, int]]],  # list of runs, each [(symbol, rank), ...]
    timestamp: str,
) -> dict:
    """Measure rank stability across multiple LLM runs."""
    import numpy as np
    from scipy.stats import spearmanr

    n_runs = len(rankings)
    if n_runs < 2:
        return {"n_runs": n_runs, "mean_rank_correlation": 1.0}

    correlations = []
    top5_overlaps = []
    for i in range(n_runs):
        for j in range(i + 1, n_runs):
            r1 = dict(rankings[i])
            r2 = dict(rankings[j])
            common = [s for s in r1 if s in r2]
            if len(common) >= 3:
                rho, _ = spearmanr([r1[s] for s in common], [r2[s] for s in common])
                correlations.append(float(rho) if not np.isnan(rho) else 0.0)
            top1set = {s for s, _ in rankings[i][:5]}
            top2set = {s for s, _ in rankings[j][:5]}
            top5_overlaps.append(len(top1set & top2set))

    return {
        "n_runs": n_runs,
        "mean_rank_correlation": round(float(np.mean(correlations)), 4) if correlations else 1.0,
        "mean_top5_overlap": round(float(np.mean(top5_overlaps)), 1) if top5_overlaps else 5.0,
    }
