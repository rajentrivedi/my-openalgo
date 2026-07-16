"""
Historical candidate dataset generator.
Creates the critical research asset: scanner signals + forward returns at every decision timestamp.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .. import config
from ..backtest.engine import _compute_nifty_signals, _compute_rich_signals, _init_scanner_instances
from ..data.stock_data import load_symbols, load_universe_data, print_data_quality
from ..scanners import (  # noqa: F401
    hilega_milega,
    mean_reversion,
    momentum,
    multi_tf_trend,
    nifty_regime,
    range_breakout,
    relative_strength,
    swing_mtf,
    volatility_contraction,
    volume_spike,
)
from ..scanners.scanner_adapter import BaseScanner
from ..scanners.scanner_ensemble import build_ranked_candidates, compute_consensus
from ..scanners.scanner_registry import get_scanner, list_scanners
from ..scanners.scanner_signal import (
    NormalizedSignal,
    RankedCandidate,
    ScannerConsensus,
    ScannerSignal,
)


def generate_candidate_dataset(
    sample: int = 50,
    use_cache: bool = False,
    start: str = "2026-07-01",
    end: str = "2026-09-11",
    output_dir: str | None = None,
) -> pd.DataFrame:
    """
    Generate the comprehensive historical candidate dataset.

    For every decision timestamp (every 30 min during market hours):
    1. Run all 10 scanners
    2. Normalize scores
    3. Build consensus
    4. Forward returns
    5. Record everything

    Returns a DataFrame with ALL candidate information.
    """
    out_dir = output_dir or config.make_out_dir()
    os.makedirs(out_dir, exist_ok=True)

    t0 = datetime.now()
    print(f"\n{'=' * 90}")
    print("  HISTORICAL CANDIDATE DATASET GENERATOR")
    print(f"  Period: {start} → {end} | Sample: {sample} symbols")
    print(f"{'=' * 90}")

    # Load data
    symbols = load_symbols()[:sample]
    raw, nifty_raw, quality = load_universe_data(symbols, use_cache, start, end)
    print_data_quality(quality)

    if not raw:
        print("ERROR: No data loaded")
        return pd.DataFrame()

    # Precompute signals
    p = config.RegimeV3Params()
    sigs = {}
    for sym, df in raw.items():
        sig = _compute_rich_signals(df, p)
        sigs[sym] = sig

    nifty_sig = _compute_nifty_signals(nifty_raw, p) if nifty_raw is not None else None

    # Build event timeline (all decision timestamps)
    scan_times = set()
    for _sym, sig in sigs.items():
        for ts in sig.index:
            ist = ts + pd.Timedelta(hours=5, minutes=30) if ts.tz is None else ts
            mm = int(ist.strftime("%H:%M").split(":")[1])
            if ist.hour >= 9 and ist.hour < 15 and mm in (0, 30):
                scan_times.add(ts)

    scan_times = sorted(scan_times)
    print(f"Decision timestamps (scan times only): {len(scan_times)}")

    scanner_instances = _init_scanner_instances()

    all_rows: list[dict] = []
    all_candidates: list[RankedCandidate] = []

    for idx, ts in enumerate(scan_times):
        if idx % 20 == 0:
            print(f"  [{idx}/{len(scan_times)}] {ts}")

        # Skip warmup timestamps
        actual_start_ts = pd.Timestamp(start, tz=timezone(timedelta(hours=5, minutes=30)))
        if ts < actual_start_ts:
            continue

        # Run scanner pipeline (use full precomputed sigs, not sliced)
        raw_by_scanner: dict[str, dict[str, ScannerSignal]] = {}
        for name, scanner in scanner_instances.items():
            try:
                signals = scanner.compute_signals(sigs, nifty_raw, ts)
                if signals:
                    raw_by_scanner[name] = signals
            except Exception:
                pass

        # Normalize
        normalized: dict[str, dict[str, NormalizedSignal]] = {}
        for name, scanner in scanner_instances.items():
            signals_for_scanner = raw_by_scanner.get(name, {})
            if signals_for_scanner:
                ns = scanner.normalize_scores(signals_for_scanner)
                for sym, sig in ns.items():
                    if sym not in normalized:
                        normalized[sym] = {}
                    normalized[sym][name] = sig

        # Consensus
        if not normalized:
            continue
        consensi = compute_consensus(normalized, ts)

        # Regime
        if nifty_sig is not None:
            from ..regime.regime_engine import compute_regime
            regime = compute_regime(nifty_sig, ts)
        else:
            regime = config.SIDEWAYS

        # Candidates
        candidates = build_ranked_candidates(consensi, regime, ts)
        all_candidates.extend(candidates)

        # Forward return labels
        for c in candidates:
            row = c.to_dict()

            # Forward returns
            if c.symbol in sigs:
                sig = sigs[c.symbol]
                sig_after = sig[sig.index > ts]
                if len(sig_after) >= 24:
                    for h in [1, 3, 6, 12, 24]:
                        if len(sig_after) >= h:
                            fwd_close = sig_after.iloc[h - 1]["close"]
                            cur_close = sig.loc[ts]["close"]
                            fwd_ret = float(fwd_close / cur_close - 1)
                            row[f"forward_return_{h}"] = round(fwd_ret * 100, 4)

            # NIFTY forward returns
            if nifty_sig is not None:
                nifty_after = nifty_sig[nifty_sig.index > ts]
                if len(nifty_after) >= 6:
                    for h in [1, 3, 6, 12, 24]:
                        if len(nifty_after) >= h:
                            fwd_nifty = float(nifty_after.iloc[h - 1]["close"])
                            cur_nifty = float(nifty_sig.loc[nifty_sig.index <= ts].iloc[-1]["close"])
                            nifty_ret = fwd_nifty / cur_nifty - 1
                            row[f"nifty_forward_return_{h}"] = round(nifty_ret * 100, 4)
                            stock_fwd = row.get(f"forward_return_{h}", 0)
                            row[f"excess_return_{h}"] = round(stock_fwd - nifty_ret * 100, 4)

            # Per-scanner scores
            if c.symbol in normalized:
                for name, ns in normalized[c.symbol].items():
                    row[f"scanner_{name}_score"] = ns.normalized_score
                    row[f"scanner_{name}_raw"] = ns.raw_score
                    row[f"scanner_{name}_dir"] = ns.direction
                    row[f"scanner_{name}_conf"] = ns.confidence

            row["timestamp"] = ts
            all_rows.append(row)

    result = pd.DataFrame(all_rows)

    # Save
    result.to_csv(f"{out_dir}/historical_candidates.csv", index=False)
    if len(result) > 0:
        result.to_parquet(f"{out_dir}/historical_candidates.parquet")

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\nDataset: {len(result)} rows, {len(result.columns)} columns")
    print(f"Saved to {out_dir}/historical_candidates.csv / .parquet")
    print(f"Elapsed: {elapsed:.0f}s")

    return result


def hhmm_to_minutes(hhmm: str) -> int:
    parts = hhmm.split(":")
    return int(parts[0]) * 60 + int(parts[1])
