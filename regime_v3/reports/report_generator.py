"""
Report generator — creates machine-readable CSV outputs and human-readable daily summary.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..portfolio.portfolio import Portfolio
from ..scanners.scanner_signal import RankedCandidate


def generate_summary_json(
    results: dict,
    params: config.RegimeV3Params,
) -> dict:
    """Generate a summary JSON with all key metrics."""
    metrics = results.get("metrics", {})
    trades = results.get("trades", pd.DataFrame())
    equity = results.get("equity", pd.DataFrame())
    results.get("candidates", pd.DataFrame())

    # Regime breakdown
    regime_breakdown = {}
    if not trades.empty and "regime" in trades.columns:
        for regime in config.ALL_REGIMES:
            sub = trades[trades["regime"] == regime]
            if not sub.empty:
                regime_breakdown[regime] = {
                    "trades": len(sub),
                    "net": float(sub["net_pnl"].sum()),
                    "wr": float((sub["net_pnl"] > 0).mean() * 100),
                }

    # Selection method breakdown
    method_breakdown = {}
    if not trades.empty and "selection_method" in trades.columns:
        for method in trades["selection_method"].unique():
            sub = trades[trades["selection_method"] == method]
            method_breakdown[method] = {
                "trades": len(sub),
                "net": float(sub["net_pnl"].sum()),
                "wr": float((sub["net_pnl"] > 0).mean() * 100),
            }

    # Drawdown
    max_dd = 0.0
    if not equity.empty and "drawdown_pct" in equity.columns:
        max_dd = float(equity["drawdown_pct"].max())

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "selection_method": params.selection_method,
            "top_n": params.top_n,
            "risk_per_trade_pct": params.risk_per_trade_pct,
            "max_trades_per_day": params.max_trades_per_day,
            "estimated_cost_pct": params.estimated_cost_pct,
            "slippage_pct": params.slippage_pct,
            "max_candidates": params.max_candidates,
            "llm_enabled": params.llm_enabled,
        },
        "overall": {
            "n": metrics.get("n", 0),
            "net_pnl": metrics.get("net", 0),
            "win_rate": metrics.get("wr", 0),
            "profit_factor": metrics.get("pf", 0),
            "avg_r": metrics.get("avg_r", 0),
            "max_drawdown_pct": max_dd,
            "gross_pnl": metrics.get("gross", 0),
            "total_cost": metrics.get("cost", 0),
        },
        "regime_breakdown": regime_breakdown,
        "selection_method_breakdown": method_breakdown,
        "llm_value_add": results.get("llm_value_add", {}),
    }


def write_all_outputs(
    results: dict,
    params: config.RegimeV3Params,
    out_dir: str | None = None,
):
    """
    Write all required output files.
    """
    d = out_dir or config.make_out_dir()
    os.makedirs(d, exist_ok=True)

    trades = results.get("trades", pd.DataFrame())
    equity = results.get("equity", pd.DataFrame())
    candidates = results.get("candidates", pd.DataFrame())
    regime_df = results.get("regime", pd.DataFrame())
    results.get("portfolio")

    # Trades
    if not trades.empty:
        trades.to_csv(f"{d}/trades.csv", index=False)

    # Equity curve
    if not equity.empty:
        equity.to_csv(f"{d}/equity.csv", index=False)

    # Candidate log
    if not candidates.empty:
        candidates.to_csv(f"{d}/ensemble_candidates.csv", index=False)

    # Regime history
    if not regime_df.empty:
        regime_df.to_csv(f"{d}/regime_history.csv", index=False)

    # Scanner inventory (metadata)
    inv_rows = []
    for name in config.SCANNER_NAMES:
        inv_rows.append({
            "scanner_name": name,
            "enabled": True,
            "timeframe": config.TIMEFRAME,
        })
    pd.DataFrame(inv_rows).to_csv(f"{d}/scanner_inventory.csv", index=False)

    # Summary JSON
    summary = generate_summary_json(results, params)
    with open(f"{d}/summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Final report markdown
    _write_report(results, params, d)


def _write_report(results: dict, params: config.RegimeV3Params, d: str):
    """Write final report markdown."""
    metrics = results.get("metrics", {})
    results.get("trades", pd.DataFrame())
    summary = generate_summary_json(results, params)

    report = f"""# Regime V3 + 10-Scanner Ensemble + LLM Meta-Ranker — Research Report

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Selection method:** {params.selection_method}
**Top N:** {params.top_n}
**Cost:** {params.estimated_cost_pct}% | **Slippage:** {params.slippage_pct}%

## 1. Overall Results

| Metric | Value |
|--------|-------|
| Total Trades | {metrics.get('n', 0)} |
| Net P&L | ₹{metrics.get('net', 0):.2f} |
| Gross P&L | ₹{metrics.get('gross', 0):.2f} |
| Total Cost | ₹{metrics.get('cost', 0):.2f} |
| Win Rate | {metrics.get('wr', 0):.1f}% |
| Profit Factor | {metrics.get('pf', 0):.3f} |
| Avg R Multiple | {metrics.get('avg_r', 0):.3f} |
| Max Drawdown | {summary['overall']['max_drawdown_pct']:.2f}% |

## 2. Regime Breakdown

| Regime | Trades | Net P&L | Win Rate |
|--------|--------|---------|----------|
"""
    for regime in config.ALL_REGIMES:
        rb = summary.get("regime_breakdown", {}).get(regime, {})
        report += f"| {regime} | {rb.get('trades', 0)} | ₹{rb.get('net', 0):.2f} | {rb.get('wr', 0):.1f}% |\n"

    # Selection method comparison
    report += "\n## 3. Selection Method Comparison\n\n"
    if summary.get("llm_value_add"):
        lva = summary["llm_value_add"]
        report += f"""| Method | Net P&L | Win Rate | PF |
|--------|---------|----------|-----|
| Random | ₹{lva.get('random_net', 0):.2f} | — | — |
| Ensemble | ₹{lva.get('ensemble_net', 0):.2f} | {lva.get('ensemble_wr', 0):.1f}% | {lva.get('ensemble_pf', 0):.3f} |
| LLM | ₹{lva.get('llm_net', 0):.2f} | {lva.get('llm_wr', 0):.1f}% | {lva.get('llm_pf', 0):.3f} |
| LLM vs Ensemble | ₹{lva.get('llm_vs_ensemble', 0):+.2f} | — | — |
"""

    report += "\n## 4. Files Generated\n\n"
    for f in sorted(os.listdir(d)):
        if f.endswith((".csv", ".json")):
            report += f"- {f}\n"

    report += "\n---\n*This is an automated research report. No investment decisions should be based solely on these results.*\n"

    with open(f"{d}/final_report.md", "w") as f:
        f.write(report)
