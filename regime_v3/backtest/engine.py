"""
Regime V3 Ensemble Backtest Engine — orchestrates the full pipeline.
Runs chronological simulation: data → scanners → normalize → ensemble → regime → rank → select → trade.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..data.stock_data import (
    IST as _DATA_IST,
)
from ..data.stock_data import (
    load_symbols,
    load_universe_data,
    print_data_quality,
    resample_timeframe,
)
from ..meta import ranking_logger
from ..meta.deterministic_ranker import (
    compute_rank_similarity,
    consensus_selection,
    random_selection,
)
from ..meta.llm_ranker import deterministic_fallback, llm_rank_candidates
from ..portfolio.portfolio import Portfolio, Position, Trade
from ..regime.regime_engine import compute_regime
from ..scanners import (  # noqa: F401 — triggers @register decorators
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
from ..scanners.scanner_registry import get_scanner, list_scanners, register, registered_count
from ..scanners.scanner_signal import (
    NormalizedSignal,
    RankedCandidate,
    ScannerConsensus,
    ScannerSignal,
    experiment_registry_entry,
)
from ..strategies.bear_momentum import bear_entry_signal
from ..strategies.bull_momentum import bull_entry_signal
from ..strategies.sideways_mean_reversion import sideways_entry_signal


def run_backtest(
    params: config.RegimeV3Params | None = None,
    sample: int = 30,
    use_cache: bool = False,
    start: str = "2026-07-01",
    end: str = "2026-09-11",
    selection_method: str | None = None,
) -> dict:
    """
    Run full Regime V3 + Ensemble + LLM backtest.

    Returns dict with trades, equity, candidates, agents, metrics.
    """
    if params is None:
        params = config.RegimeV3Params()
    if selection_method:
        params = params.clone_with(selection_method=selection_method)

    out_dir = config.make_out_dir()

    # --- Load data ---
    symbols = load_symbols()[:sample]
    raw, nifty_raw, quality = load_universe_data(symbols, use_cache, start, end)
    print_data_quality(quality)
    if not raw:
        return {"error": "no data"}

    # --- Precompute signals per symbol (full history) ---
    sigs = {}
    _import_ta()
    for sym, df in raw.items():
        sig = _compute_rich_signals(df, params)
        sigs[sym] = sig

    nifty_sig = _compute_nifty_signals(nifty_raw, params) if nifty_raw is not None else None

    # --- Chronological event loop ---
    events = defaultdict(list)
    for sym, sig in sigs.items():
        for i in range(len(sig)):
            events[sig.index[i]].append((sym, i))

    all_ts = sorted(events.keys())

    # Filter out warmup timestamps (before the actual start date)
    actual_start = pd.Timestamp(start, tz=_DATA_IST) if start else None
    if actual_start:
        all_ts = [ts for ts in all_ts if ts >= actual_start]
    if not all_ts:
        return {"error": "no timestamps after warmup"}

    portfolio = Portfolio(params)
    candidate_log: list[RankedCandidate] = []
    regime_history: list[dict] = []
    equity_rows: list[dict] = []

    all_scanner_instances = _init_scanner_instances()

    for ts in all_ts:
        ist = _to_ist(ts)
        day = ist.strftime("%Y-%m-%d")
        hhmm = ist.strftime("%H:%M")

        regime = compute_regime(nifty_sig, ts, confirm_bars=params.regime_confirm_bars)
        regime_history.append({"timestamp": ts, "regime": regime})

        # Reset day
        current_prices = {sym: {"close": float(sigs[sym].loc[ts]["close"]) if ts in sigs[sym].index else 0} for sym in raw}
        flat_prices = {sym: p["close"] for sym, p in current_prices.items()}
        portfolio.reset_day(day, flat_prices)

# Manage existing positions
        rich_prices = {sym: {k: float(v) for k, v in sigs[sym].loc[ts].items() if k in ["open","high","low","close","volume"]} for sym in raw if ts in sigs[sym].index}
        force_so = hhmm >= params.square_off_time
        portfolio.manage_positions(rich_prices, ts, force_square_off=force_so)

        # Daily loss check
        if portfolio.check_daily_loss(flat_prices):
            portfolio.close_all_positions(flat_prices, "DAILY_LOSS")

        # --- RUN SCANNERS (every 30 min, only after sufficient history) ---
        run_scanners_now = _should_scan(hhmm) and portfolio.can_trade(flat_prices)
        if run_scanners_now:
            # DIRECT pipeline test removed
            candidates = _run_scanner_pipeline(
                all_scanner_instances, sigs, nifty_raw, ts, regime, params
            )

            if candidates:
                for c in candidates:
                    candidate_log.append(c)

                # LLM or deterministic selection
                if params.selection_method == config.SELECT_LLM_TOP:
                    ranked = llm_rank_candidates(
                        candidates, regime, ts.strftime("%Y-%m-%dT%H:%M:%S+05:30")
                    )
                    if ranked is None:
                        ranked = deterministic_fallback(candidates, params.top_n)
                elif params.selection_method == config.SELECT_RANDOM:
                    ranked = random_selection(candidates, params.top_n)
                elif params.selection_method == config.SELECT_CONSENSUS_TOP:
                    ranked = consensus_selection(candidates, params.top_n)
                else:
                    ranked = deterministic_fallback(candidates, params.top_n)

                # --- Entry ---
                for c in ranked:
                    if not c.llm_selected or c.direction == "NEUTRAL":
                        continue
                    if hhmm >= params.entry_cutoff:
                        continue

                    idx = list(sigs[c.symbol].index).index(ts)
                    if idx == 0:
                        continue

                    prev = sigs[c.symbol].iloc[idx - 1]
                    candle = sigs[c.symbol].iloc[idx]
                    entry_open = float(candle["open"])

                    if nifty_sig is not None:
                        (nifty_sig.loc[:ts].iloc[-1]["close"] >
                                      nifty_sig.loc[:ts].iloc[-1].get("ema20", 0)) if len(nifty_sig.loc[:ts]) else None

                    entry_data = None
                    strat_name = config.STRATEGY_NONE
                    if regime == config.BULL:
                        entry_data = bull_entry_signal(candle, prev, params)
                        strat_name = config.STRATEGY_BULL
                    elif regime == config.BEAR:
                        entry_data = bear_entry_signal(candle, prev, params)
                        strat_name = config.STRATEGY_BEAR
                    elif regime == config.SIDEWAYS:
                        entry_data = sideways_entry_signal(prev, candle, params)
                        strat_name = config.STRATEGY_SIDEWAYS

                    if entry_data is None:
                        continue

                    direction, stop, target, _ = entry_data
                    slippage = entry_open * (1 + params.slippage_pct / 100) if direction == "LONG" else entry_open * (1 - params.slippage_pct / 100)

                    portfolio.open_position(
                        symbol=c.symbol,
                        direction=direction,
                        entry_price=slippage,
                        entry_time=ts,
                        stop_price=stop,
                        target_price=target,
                        current_prices=flat_prices,
                        selection_method=c.selection_method,
                        llm_score=c.llm_score,
                        strategy=strat_name,
                        regime=regime,
                    )

        # Equity log
        total_eq = portfolio.total_equity(flat_prices)
        portfolio.peak_equity = max(portfolio.peak_equity, total_eq)
        dd_pct = (portfolio.peak_equity - total_eq) / max(portfolio.peak_equity, 1) * 100
        equity_rows.append({
            "timestamp": ts,
            "realized_pnl": portfolio.realized_pnl,
            "total_equity": total_eq,
            "drawdown_pct": round(float(dd_pct), 4),
            "open_positions": len(portfolio.positions),
            "daily_trade_count": portfolio._daily_trade_count,
            "regime": regime,
        })

    # --- Final metrics ---
    metrics = portfolio.get_metrics()
    trades_df = pd.DataFrame([t.to_dict() for t in portfolio.trades]) if portfolio.trades else pd.DataFrame()
    equity_df = pd.DataFrame(equity_rows) if equity_rows else pd.DataFrame()
    regime_df = pd.DataFrame(regime_history) if regime_history else pd.DataFrame()
    candidates_df = pd.DataFrame([c.to_dict() for c in candidate_log]) if candidate_log else pd.DataFrame()

    # Save outputs
    if not trades_df.empty:
        trades_df.to_csv(f"{out_dir}/trades.csv", index=False)
    if not equity_df.empty:
        equity_df.to_csv(f"{out_dir}/equity.csv", index=False)
    if not regime_df.empty:
        regime_df.to_csv(f"{out_dir}/regime_history.csv", index=False)
    if not candidates_df.empty:
        candidates_df.to_csv(f"{out_dir}/ensemble_candidates.csv", index=False)

    return {
        "trades": trades_df,
        "equity": equity_df,
        "regime": regime_df,
        "candidates": candidates_df,
        "metrics": metrics,
        "portfolio": portfolio,
    }


def _should_scan(hhmm: str) -> bool:
    """Run scanners at 10:00 and 13:00 only (for speed)."""
    return hhmm in ("10:00", "13:00")


def _init_scanner_instances() -> dict[str, BaseScanner]:
    instances = {}
    for name in list_scanners():
        cls = get_scanner(name)
        instances[name] = cls()
    return instances


def _run_scanner_pipeline(
    scanner_instances: dict[str, BaseScanner],
    symbol_data: dict[str, pd.DataFrame],
    nifty_data: pd.DataFrame | None,
    timestamp: pd.Timestamp,
    regime: str,
    params: config.RegimeV3Params,
) -> list[RankedCandidate]:
    """Run all scanners, normalize, build consensus, return ranked candidates."""
    # Phase 1: raw signals — keyed by scanner name
    raw_signals_by_scanner: dict[str, dict[str, ScannerSignal]] = {}
    for name, scanner in scanner_instances.items():
        try:
            signals = scanner.compute_signals(symbol_data, nifty_data, timestamp)
            raw_signals_by_scanner[name] = signals
        except Exception:
            pass

    # Phase 2: normalize (cross-sectional per scanner)
    normalized: dict[str, dict[str, NormalizedSignal]] = {}
    for name, scanner in scanner_instances.items():
        try:
            ns = scanner.normalize_scores(raw_signals_by_scanner.get(name, {}))
            for sym, sig in ns.items():
                if sym not in normalized:
                    normalized[sym] = {}
                normalized[sym][name] = sig
        except Exception:
            pass

    # Phase 3: consensus
    consensi = compute_consensus(normalized, timestamp)

    # Phase 4: ranked candidates
    candidates = build_ranked_candidates(consensi, regime, timestamp)

    return candidates[:params.max_candidates]


def _import_ta():
    """Make sure openalgo.ta is importable."""
    try:
        from openalgo import ta
    except ImportError:
        pass


def _compute_rich_signals(
    df: pd.DataFrame,
    p: config.RegimeV3Params,
) -> pd.DataFrame:
    """Compute indicators used by strategies and scanners."""
    sig = df.copy()
    try:
        from openalgo import ta as _ta
        ta_available = True
    except ImportError:
        ta_available = False

    for c in ["open", "high", "low", "close", "volume"]:
        sig[c] = pd.to_numeric(sig[c], errors="coerce")

    atr_len = getattr(p, "atr_length", config.ATR_LENGTH)
    if ta_available:
        sig["atr"] = _ta.atr(sig["high"], sig["low"], sig["close"], atr_len)
    else:
        hl = sig["high"] - sig["low"]
        hc = (sig["high"] - sig["close"].shift()).abs()
        lc = (sig["low"] - sig["close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        sig["atr"] = tr.ewm(span=atr_len, adjust=False).mean()
    sig["vwap"] = (sig["volume"] * (sig["high"] + sig["low"] + sig["close"]) / 3).cumsum() / sig["volume"].cumsum().clip(lower=1e-10)

    # EMA
    sig["ema9"] = sig["close"].ewm(span=config.EMA9_LEN, adjust=False).mean()
    sig["ema20"] = sig["close"].ewm(span=config.EMA20_LEN, adjust=False).mean()
    sig["ema50"] = sig["close"].ewm(span=config.EMA50_LEN, adjust=False).mean()
    sig["ema20_slope"] = (sig["ema20"] - sig["ema20"].shift(config.EMA_SLOPE_BARS)) / sig["atr"].clip(lower=1e-10)

    # Volume
    vol_lookback = getattr(p, "volume_lookback", config.VOLUME_LOOKBACK)
    sig["avg_volume"] = sig["volume"].rolling(vol_lookback).mean()
    sig["rel_volume"] = sig["volume"] / sig["avg_volume"].clip(lower=1e-10)
    sig["volume_percentile"] = sig["volume"].rank(pct=True)

    # Returns
    for bars in [1, 3, 5, 10, 15, 20]:
        sig[f"return_{bars}"] = sig["close"].pct_change(bars)

    # Structure flags
    sig["bull_struct"] = (sig["close"] > sig["ema20"]) & (sig["ema20"] > sig["ema50"])
    sig["bear_struct"] = (sig["close"] < sig["ema20"]) & (sig["ema20"] < sig["ema50"])

    # Displacement
    sig["long_displacement"] = (sig["close"] - sig["ema20"]) / sig["atr"].clip(lower=1e-10)
    sig["short_displacement"] = (sig["ema20"] - sig["close"]) / sig["atr"].clip(lower=1e-10)

    # Range
    range_bars = getattr(p, "range_bars", config.RANGE_BARS)
    sig["range_high"] = sig["high"].rolling(range_bars).max()
    sig["range_low"] = sig["low"].rolling(range_bars).min()
    sig["range_width"] = sig["range_high"] - sig["range_low"]

    # Candle
    sig["body_size"] = (sig["close"] - sig["open"]).abs()
    sig["body_atr_ratio"] = sig["body_size"] / sig["atr"].clip(lower=1e-10)
    _hl = sig["high"] - sig["low"]
    sig["close_loc"] = (sig["close"] - sig["low"]) / _hl.clip(lower=1e-10)

    return sig


def _compute_nifty_signals(df: pd.DataFrame, p: config.RegimeV3Params) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    sig = df.copy()
    try:
        from openalgo import ta as _ta
        ta_avail = True
    except ImportError:
        ta_avail = False
    for c in ["close", "high", "low"]:
        sig[c] = pd.to_numeric(sig[c], errors="coerce")
    atr_len = getattr(p, "atr_length", config.ATR_LENGTH)
    if ta_avail:
        sig["atr"] = _ta.atr(sig["high"], sig["low"], sig["close"], atr_len)
    else:
        hl = sig["high"] - sig["low"]
        hc = (sig["high"] - sig["close"].shift()).abs()
        lc = (sig["low"] - sig["close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        sig["atr"] = tr.ewm(span=atr_len, adjust=False).mean()
    sig["ema20"] = sig["close"].ewm(span=config.EMA20_LEN, adjust=False).mean()
    sig["ema50"] = sig["close"].ewm(span=config.EMA50_LEN, adjust=False).mean()
    sig["ema20_slope"] = (sig["ema20"] - sig["ema20"].shift(config.EMA_SLOPE_BARS)) / sig["atr"].clip(lower=1e-10)
    return sig


def _to_ist(ts) -> datetime:
    """Convert timestamp to IST datetime."""
    from datetime import timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    return ts.astimezone(ist) if hasattr(ts, "astimezone") else ts


def run_walkforward(
    params: config.RegimeV3Params,
    n_windows: int = 4,
    sample: int = 30,
    use_cache: bool = False,
    start: str = "2026-07-01",
    end: str = "2026-09-11",
) -> pd.DataFrame:
    """Run walk-forward analysis with N chronological windows."""
    symbols = load_symbols()[:sample]
    raw, nifty_raw, quality = load_universe_data(symbols, use_cache, start, end)

    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    rows = []

    for w in range(n_windows):
        wf_start = all_ts[int(n * (0.3 + w * 0.10))]
        wf_end = all_ts[min(int(n * (0.4 + w * 0.10)), n - 1)]

        wr = {s: d[(d.index >= wf_start) & (d.index < wf_end)] for s, d in raw.items() if ((d.index >= wf_start) & (d.index < wf_end)).any()}
        wn = nifty_raw[(nifty_raw.index >= wf_start) & (nifty_raw.index < wf_end)] if nifty_raw is not None else None
        if not wr:
            continue

        sub_params = params.clone_with(selection_method=config.SELECT_ENSEMBLE_TOP)
        result = _run_sub_backtest(wr, wn, sub_params, wf_start.strftime("%Y-%m-%d"), wf_end.strftime("%Y-%m-%d"))
        m = result.get("metrics", {})
        rows.append({
            "window": w + 1,
            "start": wf_start,
            "end": wf_end,
            "n": m.get("n", 0),
            "net": m.get("net", 0),
            "pf": m.get("pf", 0),
            "wr": m.get("wr", 0),
            "avg_r": m.get("avg_r", 0),
            "max_dd": m.get("max_dd", 0),
        })

    return pd.DataFrame(rows)


def _run_sub_backtest(raw, nifty_raw, params, start_label, end_label) -> dict:
    """Minimal backtest for a sub-period."""
    sigs = {}
    for sym, df in raw.items():
        sigs[sym] = _compute_rich_signals(df, params)
    nifty_sig = _compute_nifty_signals(nifty_raw, params) if nifty_raw is not None else None

    events = defaultdict(list)
    for sym, sig in sigs.items():
        for i in range(len(sig)):
            events[sig.index[i]].append((sym, i))

    portfolio = Portfolio(params)
    scanner_instances = _init_scanner_instances()

    for ts in sorted(events):
        flat_prices = {sym: float(sigs[sym].loc[ts]["close"]) if ts in sigs[sym].index else 0 for sym in sigs}
        ist = _to_ist(ts)
        hhmm = ist.strftime("%H:%M")
        day = ist.strftime("%Y-%m-%d")

        regime = compute_regime(nifty_sig, ts, confirm_bars=params.regime_confirm_bars)
        portfolio.reset_day(day, flat_prices)
        rich_prices = {sym: {k: float(v) for k, v in sigs[sym].loc[ts].items() if k in ["open","high","low","close"]} for sym in sigs if ts in sigs[sym].index}
        portfolio.manage_positions(rich_prices, ts, force_so=(hhmm >= params.square_off_time))
        if portfolio.check_daily_loss(flat_prices):
            portfolio.close_all_positions(flat_prices, "DAILY_LOSS")

        ref_sym = list(sigs.keys())[0] if sigs else ""
        has_history = (ts in sigs.get(ref_sym, pd.DataFrame()).index and
                       sigs[ref_sym].index.get_loc(ts) >= 60) if ref_sym else False
        if _should_scan(hhmm) and has_history and portfolio.can_trade(flat_prices):
            candidates = _run_scanner_pipeline(scanner_instances, sigs, nifty_raw, ts, regime, params)
            if candidates:
                ranked = deterministic_fallback(candidates, params.top_n)
                for c in ranked:
                    if not c.llm_selected or c.direction == "NEUTRAL" or hhmm >= params.entry_cutoff:
                        continue
                    if c.symbol not in sigs or ts not in sigs[c.symbol].index:
                        continue
                    idx = list(sigs[c.symbol].index).index(ts)
                    if idx == 0:
                        continue
                    prev = sigs[c.symbol].iloc[idx - 1]
                    candle = sigs[c.symbol].iloc[idx]
                    entry_data = None
                    if regime == config.BULL:
                        entry_data = bull_entry_signal(candle, prev, params)
                    elif regime == config.BEAR:
                        entry_data = bear_entry_signal(candle, prev, params)
                    elif regime == config.SIDEWAYS:
                        entry_data = sideways_entry_signal(prev, candle, params)
                    if entry_data is None:
                        continue
                    direction, stop, target, _ = entry_data
                    portfolio.open_position(c.symbol, direction, float(candle["open"]), ts, stop, target, flat_prices, c.selection_method, llm_score=c.llm_score)

    return {"metrics": portfolio.get_metrics(), "trades": portfolio.trades}


_RESEARCH_MEMO = None
