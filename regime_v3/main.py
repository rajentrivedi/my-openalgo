"""
Regime V3 + 10-Scanner Ensemble + LLM Meta-Ranker — Main entrypoint.

Usage:
    uv run python -m regime_v3.main                       # full run with LLM
    uv run python -m regime_v3.main --sample 10           # subset
    uv run python -m regime_v3.main --no-llm              # deterministic only
    uv run python -m regime_v3.main --cache               # use cached data
    uv run python -m regime_v3.main --method random       # random selection
    uv run python -m regime_v3.main --method ensemble     # ensemble only
    uv run python -m regime_v3.main --method consensus    # consensus ranking
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from . import config
from .backtest.engine import run_backtest, run_walkforward
from .data.stock_data import load_symbols, load_universe_data
from .reports.report_generator import generate_summary_json, write_all_outputs
from .research.attribution import compute_llm_value_add, scanner_attribution
from .research.robustness import monte_carlo, symbol_stress_test, time_of_day_analysis
from .scanners.scanner_registry import list_scanners, registered_count


def main():
    parser = argparse.ArgumentParser(
        description="Regime V3 + 10-Scanner Ensemble + LLM Meta-Ranker"
    )
    parser.add_argument("--sample", type=int, default=30, help="Number of symbols")
    parser.add_argument("--cache", action="store_true", help="Use cached data")
    parser.add_argument("--start", default="2026-07-01", help="Start date")
    parser.add_argument("--end", default="2026-09-11", help="End date")
    parser.add_argument("--method", default="llm", choices=["llm", "ensemble", "random", "consensus"],
                        help="Selection method")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM (use deterministic)")
    parser.add_argument("--top-n", type=int, default=5, help="Top N stocks to select")
    parser.add_argument("--walkforward", action="store_true", help="Run walk-forward analysis")
    parser.add_argument("--mc", action="store_true", help="Run Monte Carlo simulation")
    parser.add_argument("--scan-list", action="store_true", help="List registered scanners and exit")

    args = parser.parse_args()

    if args.scan_list:
        print(f"Registered scanners ({registered_count()}):")
        for name in list_scanners():
            print(f"  - {name}")
        sys.exit(0)

    # Configuration
    method_map = {
        "llm": config.SELECT_LLM_TOP,
        "ensemble": config.SELECT_ENSEMBLE_TOP,
        "random": config.SELECT_RANDOM,
        "consensus": config.SELECT_CONSENSUS_TOP,
    }

    if args.no_llm and args.method == "llm":
        selection = config.SELECT_ENSEMBLE_TOP
    else:
        selection = method_map.get(args.method, config.SELECT_LLM_TOP)

    p = config.RegimeV3Params(
        selection_method=selection,
        top_n=args.top_n,
        llm_enabled=not args.no_llm and selection == config.SELECT_LLM_TOP,
    )

    t0 = datetime.now()

    print("=" * 90)
    print("  REGIME V3 + 10-SCANNER ENSEMBLE + LLM META-RANKER")
    print(f"  Method: {selection} | Top-N: {args.top_n} | Sample: {args.sample}")
    print(f"  Period: {args.start} → {args.end}")
    print(f"  Scanners: {registered_count()} registered")
    print("=" * 90)

    if not os.environ.get("OPENALGO_API_KEY"):
        print("ERROR: OPENALGO_API_KEY environment variable not set")
        sys.exit(1)

    # Run primary backtest
    print(f"\nRunning backtest with {selection}...")
    result = run_backtest(
        params=p,
        sample=args.sample,
        use_cache=args.cache,
        start=args.start,
        end=args.end,
    )

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    metrics = result.get("metrics", {})
    trades = result.get("trades", pd.DataFrame())
    result.get("equity", pd.DataFrame())

    print("\nResults:")
    print(f"  Trades: {metrics.get('n', 0)}")
    print(f"  Net P&L: ₹{metrics.get('net', 0):.2f}")
    print(f"  Win Rate: {metrics.get('wr', 0):.1f}%")
    print(f"  Profit Factor: {metrics.get('pf', 0):.3f}")
    print(f"  Avg R: {metrics.get('avg_r', 0):.3f}")
    print(f"  Max DD: {metrics.get('max_dd', 0):.2f}%")

    # Comparison runs (LLM value-add)
    if selection == config.SELECT_LLM_TOP:
        print("\nRunning comparison with Ensemble method...")
        result_ensemble = run_backtest(
            params=p.clone_with(selection_method=config.SELECT_ENSEMBLE_TOP),
            sample=args.sample,
            use_cache=args.cache,
            start=args.start,
            end=args.end,
        )
        result_random = run_backtest(
            params=p.clone_with(selection_method=config.SELECT_RANDOM),
            sample=args.sample,
            use_cache=args.cache,
            start=args.start,
            end=args.end,
        )

        lva = compute_llm_value_add(result, result_ensemble, result_random)
        result["llm_value_add"] = lva
        print(f"\n  LLM Value-Add: ₹{lva.get('llm_vs_ensemble', 0):+.2f} vs Ensemble")
        print(f"  LLM vs Random: ₹{lva.get('llm_vs_random', 0):+.2f}")

    # Walk-forward
    if args.walkforward:
        print("\nRunning walk-forward analysis...")
        wf = run_walkforward(p, sample=min(args.sample, 30), use_cache=args.cache,
                             start=args.start, end=args.end)
        print(f"  Walk-forward windows: {len(wf)}")
        for _, r in wf.iterrows():
            print(f"    W{r['window']}: n={r['n']} net=₹{r['net']:.0f} pf={r['pf']:.3f}")
        wf.to_csv(f"{config.OUT_DIR}/walkforward.csv", index=False)

    # Monte Carlo
    if args.mc and not trades.empty and "r_multiple" in trades.columns:
        print("\nRunning Monte Carlo (5000 simulations)...")
        r_mults = trades["r_multiple"].dropna().tolist()
        mc = monte_carlo(r_mults, n_simulations=5000)
        print(f"  5th percentile final equity: ₹{mc.loc[mc['index']==0.05, 'final_equity'].values[0]:.0f}" if 0.05 in mc['index'].values else "  MC complete")
        mc.to_csv(f"{config.OUT_DIR}/monte_carlo.csv", index=False)

    # Symbol stats
    if not trades.empty and "symbol" in trades.columns:
        sym_stats = symbol_stress_test(trades)
        sym_stats.to_csv(f"{config.OUT_DIR}/symbol_stats.csv", index=False)
        print(f"\n  Unique symbols traded: {len(sym_stats)}")

    # Time of day
    if not trades.empty and "entry_time" in trades.columns:
        tod = time_of_day_analysis(trades)
        if not tod.empty:
            tod.to_csv(f"{config.OUT_DIR}/time_of_day.csv", index=False)

    # Write outputs
    write_all_outputs(result, p)

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\nOutputs: {config.OUT_DIR}/")
    print(f"Elapsed: {elapsed:.0f}s")

    generate_summary_json(result, p)

    print(f"\n{'=' * 90}")
    print(f"  COMPLETE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"{'=' * 90}")

    return result


if __name__ == "__main__":
    main()
