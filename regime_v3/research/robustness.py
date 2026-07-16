"""
Robustness tests — Monte Carlo, symbol stress, sector analysis, time-of-day, cost/slippage stress.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def monte_carlo(
    trade_r_multiples: list[float],
    n_simulations: int = 5000,
    risk_per_trade: float = 250.0,
    initial_capital: float = 100_000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Monte Carlo simulation by reshuffling trade R-multiples.
    Returns quantiles of final equity and max drawdown.
    """
    if not trade_r_multiples:
        return pd.DataFrame()

    rng = np.random.default_rng(seed)
    finals = []
    max_dds = []

    for _ in range(n_simulations):
        seq = rng.choice(trade_r_multiples, size=len(trade_r_multiples))
        eq = initial_capital
        peak = initial_capital
        max_dd = 0.0
        for r in seq:
            eq += r * risk_per_trade
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)
        finals.append(eq)
        max_dds.append(max_dd)

    result = pd.DataFrame({
        "final_equity": finals,
        "max_drawdown_pct": max_dds,
    })

    return result.quantile([0.05, 0.25, 0.50, 0.75, 0.95]).reset_index()


def symbol_stress_test(trades_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate metrics per symbol and test concentration.
    """
    if trades_df.empty or "symbol" not in trades_df.columns:
        return pd.DataFrame()

    sym_stats = trades_df.groupby("symbol").agg(
        trades=("net_pnl", "count"),
        net=("net_pnl", "sum"),
        wr=("net_pnl", lambda x: (x > 0).mean() * 100),
        avg_r=("r_multiple", "mean") if "r_multiple" in trades_df.columns else ("net_pnl", "mean"),
    ).reset_index()

    sym_stats["contribution_pct"] = sym_stats["net"] / max(sym_stats["net"].sum(), 1e-10) * 100
    sym_stats.sort_values("net", ascending=False, inplace=True)

    return sym_stats


def time_of_day_analysis(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Analyze trade performance by time of day."""
    if trades_df.empty or "entry_time" not in trades_df.columns:
        return pd.DataFrame()

    df = trades_df.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["hour"] = df["entry_time"].dt.hour
    df["minute"] = df["entry_time"].dt.minute
    df["time_slot"] = df["hour"] * 60 + df["minute"]

    edges = [9*60+20, 9*60+45, 10*60+15, 10*60+45, 11*60+15, 12*60, 13*60, 13*60+30, 14*60, 14*60+30, 15*60]
    labels = ["09:20-09:45", "09:45-10:15", "10:15-10:45", "10:45-11:15", "11:15-12:00", "12:00-13:00", "13:00-13:30", "13:30-14:00", "14:00-14:30", "14:30-15:00"]

    df["slot"] = pd.cut(df["time_slot"], bins=edges, labels=labels)

    result = df.groupby("slot", observed=False).agg(
        trades=("net_pnl", "count"),
        net=("net_pnl", "sum"),
        wr=("net_pnl", lambda x: (x > 0).mean() * 100),
        avg_r=("r_multiple", "mean") if "r_multiple" in df.columns else ("net_pnl", "mean"),
    ).reset_index()

    return result


def cost_slippage_stress(
    base_results: dict,
    cost_range: list[float] = None,
    slippage_range: list[float] = None,
) -> pd.DataFrame:
    """
    Analyze sensitivity to transaction costs and slippage.
    Note: This is a schema function. Actual stress requires re-running the backtest.
    """
    if cost_range is None:
        cost_range = [0.0, 0.01, 0.02, 0.03, 0.05]
    if slippage_range is None:
        slippage_range = [1.0, 1.25, 1.5, 2.0]

    # Return the schema for stress testing
    rows = []
    for cost in cost_range:
        for slip_mult in slippage_range:
            rows.append({
                "cost_pct": cost,
                "slippage_mult": slip_mult,
                "estimated_slippage_pct": round(0.02 * slip_mult, 4),
            })

    return pd.DataFrame(rows)
