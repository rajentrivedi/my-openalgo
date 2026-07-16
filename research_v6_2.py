"""
V6.2 — Signal Quality & Predictive Edge Study.
Measures forward-return distributions for momentum, RS, volume, MA features.
No strategy optimization. Pure signal research.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)

from backtest_range_breakout import (
    IST,
    LOOKBACK_DAYS,
    BacktestParams,
    compute_signals,
    load_symbols,
    load_universe_data,
    print_data_quality,
)

OUT_DIR = "diagnostics"
os.makedirs(OUT_DIR, exist_ok=True)


def _feature_dataset(df: pd.DataFrame, nifty_df: pd.DataFrame | None, sym: str) -> pd.DataFrame:
    """Build one symbol's signal dataset with features + forward returns."""
    p = BacktestParams()
    sig = compute_signals(df, p)
    if sig.empty or len(sig) < 60:
        return pd.DataFrame()

    # Feature columns
    feat = pd.DataFrame(index=sig.index)
    feat["symbol"] = sym
    for c in "close high low open volume atr vwap".split():
        feat[c] = pd.to_numeric(sig[c], errors="coerce")
    for c in "rel_volume volume_percentile".split():
        feat[c] = pd.to_numeric(sig.get(c, 0), errors="coerce")
    for c in "ema9 ema20 ema50 ema20_slope long_displacement short_displacement".split():
        feat[c] = pd.to_numeric(sig.get(c, 0), errors="coerce")
    feat["price_vs_ema20"] = (feat["close"] - feat["ema20"]) / feat["atr"].clip(lower=1e-10)
    feat["ema9_ema20"] = (feat["ema9"] - feat["ema20"]) / feat["atr"].clip(lower=1e-10)
    feat["ema20_ema50"] = (feat["ema20"] - feat["ema50"]) / feat["atr"].clip(lower=1e-10)
    feat["body_size"] = (feat["close"] - feat["open"]).abs()
    feat["body_atr"] = feat["body_size"] / feat["atr"].clip(lower=1e-10)
    feat["close_loc"] = (feat["close"] - sig["low"]) / (sig["high"] - sig["low"]).clip(lower=1e-10)

    # Momentum returns
    for bars in (1, 3, 5, 10, 15, 20):
        feat[f"return_{bars}"] = feat["close"].pct_change(bars)
        feat[f"ret_atr_{bars}"] = (feat["close"] - feat["close"].shift(bars)) / feat["atr"].clip(
            lower=1e-10
        )

    # Forward returns
    for bars, label in ((1, 1), (3, 3), (6, 6), (12, 12), (24, 24)):
        fwd = feat["close"].shift(-bars) / feat["close"] - 1
        feat[f"future_return_{label}"] = fwd

    # Candle features
    _hl = sig["high"] - sig["low"]
    _safe = _hl.clip(lower=1e-10)
    feat["candle_upper"] = (sig["high"] - sig[["close", "open"]].max(axis=1)) / _safe
    feat["candle_lower"] = (sig[["close", "open"]].min(axis=1) - sig["low"]) / _safe

    # Volatility
    feat["atr_pct"] = feat["atr"] / feat["close"].clip(lower=1e-10) * 100
    feat["range_exp"] = _hl / _safe

    feat["timestamp"] = sig.index
    return feat


def _nifty_dataset(nifty_df: pd.DataFrame) -> pd.DataFrame:
    sig = nifty_df.copy()
    for c in ("close", "high", "low"):
        sig[c] = pd.to_numeric(sig[c], errors="coerce")
    sig["atr"] = 0.0
    try:
        from openalgo import ta

        sig["atr"] = ta.atr(sig["high"], sig["low"], sig["close"], 14)
    except Exception:
        pass
    sig["ema20"] = sig["close"].ewm(span=20, adjust=False).mean()
    sig["ema50"] = sig["close"].ewm(span=50, adjust=False).mean()
    for bars in (3, 5, 10, 20):
        sig[f"nifty_return_{bars}"] = sig["close"].pct_change(bars)
    sig["nifty_ema20_slope"] = (sig["ema20"] - sig["ema20"].shift(3)) / sig["atr"].clip(lower=1e-10)
    return sig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"):
        print("ERROR: OPENALGO_API_KEY")
        sys.exit(1)

    symbols = load_symbols()[: args.sample]
    raw, nifty_raw, quality = load_universe_data(symbols, args.cache, args.start, args.end)
    print_data_quality(quality)
    if not raw:
        sys.exit(1)

    print("Building NIFTY dataset...")
    ndf = _nifty_dataset(nifty_raw) if nifty_raw is not None else None

    print("Building signal dataset...")
    all_rows = []
    num_syms = len(raw)
    for idx, (sym, df) in enumerate(raw.items()):
        if idx % 10 == 0:
            print(f"  [{idx}/{num_syms}] {sym}")
        try:
            feat = _feature_dataset(df, ndf, sym)
            if not feat.empty:
                all_rows.append(feat)
        except Exception as e:
            print(f"  [SKIP] {sym}: {e}")

    if not all_rows:
        print("No data produced!")
        return

    big = pd.concat(all_rows)
    if "timestamp" in big.columns:
        big.set_index("timestamp", inplace=True)

    # Merge NIFTY returns
    if ndf is not None:
        for c in ndf.columns:
            if c not in big.columns and c != "timestamp":
                big[c] = ndf[c]

    # Excess returns (stock - NIFTY)
    for label in (1, 3, 6, 12, 24):
        ncol = f"nifty_return_{label}" if f"nifty_return_{label}" in big.columns else None
        fcol = f"future_return_{label}"
        if ncol is not None and fcol in big.columns:
            big[f"excess_return_{label}"] = big[fcol] - big[ncol]

    print(f"Dataset: {len(big)} rows, {len(big.columns)} columns")
    big.to_parquet(f"{OUT_DIR}/v6_2_signal_dataset.parquet")
    big.head(50000).to_csv(f"{OUT_DIR}/v6_2_signal_dataset.csv", index=True)
    print(f"Saved to {OUT_DIR}/v6_2_signal_dataset.csv (sample)")

    # --- Signal Deciles ---
    print("\n=== Signal Deciles ===")
    signals = [
        "return_5",
        "return_10",
        "return_20",
        "rel_volume",
        "price_vs_ema20",
        "ema20_ema50",
        "ema20_slope",
    ]
    horizons = [1, 3, 6, 12]
    rs_signals = []
    for bars in (3, 5, 10, 20):
        col = f"nifty_return_{bars}"
        if col in big.columns:
            rs_col = f"rs_{bars}"
            big[rs_col] = big.get(f"return_{bars}", 0) - big[col]
            rs_signals.append(rs_col)

    decile_rows = {}
    for sig_name in signals + rs_signals:
        if sig_name not in big.columns:
            continue
        col = big[sig_name].dropna()
        if len(col) < 100:
            continue
        labels = [f"D{i}" for i in range(1, 11)]
        # Bucket per-symbol to avoid cross-symbol bias
        sym_deciles = {}
        for sym, grp in big.groupby("symbol"):
            s_col = grp[sig_name].dropna()
            if len(s_col) < 10:
                continue
            try:
                sym_deciles[sym] = pd.qcut(s_col, q=10, labels=labels, duplicates="drop")
            except Exception:
                pass
        if not sym_deciles:
            continue
        # Map back
        big["_dec"] = None
        for sym, dc in sym_deciles.items():
            big.loc[big["symbol"] == sym, "_dec"] = dc
        big["_dec"] = pd.Categorical(big["_dec"], categories=labels, ordered=True)
        for h in horizons:
            fcol = f"future_return_{h}"
            ecol = f"excess_return_{h}"
            if fcol not in big.columns:
                continue
            grp = big.groupby("_dec", observed=False)[fcol].agg(["mean", "median", "count"])
            grp.columns = [f"mean_{h}", f"med_{h}", f"n_{h}"]
            # win rate
            wr = big.groupby("_dec", observed=False)[fcol].apply(lambda x: (x > 0).mean() * 100)
            grp[f"wr_{h}"] = wr

            # t-stat
            def tstat(x):
                if len(x) < 3:
                    return 0.0
                return float(x.mean() / x.std() * np.sqrt(len(x))) if x.std() > 0 else 0.0

            ts = big.groupby("_dec", observed=False)[fcol].apply(tstat)
            grp[f"t_{h}"] = ts
            # excess return
            if ecol in big.columns:
                ex = big.groupby("_dec", observed=False)[ecol].apply(lambda x: x.mean() * 100)
                grp[f"excess_bp_{h}"] = ex
            decile_rows[f"{sig_name}_{h}"] = grp.reset_index()

    # Combine decile tables into one report
    with open(f"{OUT_DIR}/v6_2_signal_deciles.csv", "w") as f:
        for key, grp in decile_rows.items():
            f.write(f"\n=== {key} ===\n")
            grp.to_csv(f, index=False)

    # --- Monotonicity ---
    print("\n=== Monotonicity (Spearman rank correlation) ===")
    monotone_rows = []
    for sig_name in signals + rs_signals:
        for h in horizons:
            fcol = f"future_return_{h}"
            key = f"{sig_name}_{h}"
            if key not in decile_rows:
                continue
            grp = decile_rows[key]
            if len(grp) < 3:
                continue
            ranks = np.arange(1, len(grp) + 1)
            means = grp[f"mean_{h}"].values
            if means.std() == 0:
                continue
            rho, _ = pd.Series(ranks).corr(pd.Series(means), method="spearman"), None
            monotone_rows.append({"signal": sig_name, "horizon": h, "spearman_rho": rho})
    monodf = pd.DataFrame(monotone_rows)
    monodf.to_csv(f"{OUT_DIR}/v6_2_forward_returns.csv", index=False)
    print(monodf.to_string())

    # --- Conditional combos ---
    print("\n=== Signal Interactions ===")
    combos = [
        (["return_10"], "return_10 > 0", lambda r: r > 0, lambda r: r <= 0),
        (
            ["return_10", "rs_10"],
            "momentum>0 + RS>0",
            lambda r: r.get("return_10", 0) > 0,
            lambda r: r.get("rs_10", 0) > 0,
        ),
        (
            ["return_10", "rel_volume"],
            "momentum>0 + RV>1.5",
            lambda r: r.get("return_10", 0) > 0,
            lambda r: r.get("rel_volume", 0) >= 1.5,
        ),
        (
            ["return_10", "ema20_ema50"],
            "momentum>0 + EMA20>EMA50",
            lambda r: r.get("return_10", 0) > 0,
            lambda r: r.get("ema20_ema50", 0) > 0,
        ),
        (
            ["return_10", "rs_10", "rel_volume"],
            "mom+RS+RV",
            lambda r: r.get("return_10", 0) > 0,
            lambda r: r.get("rs_10", 0) > 0,
            lambda r: r.get("rel_volume", 0) >= 1.5,
        ),
    ]
    for needed in combos:
        name = needed[1]
        mask = pd.Series(True, index=big.index)
        if "RS>0" in name:
            mask = big.get("rs_10", 0).fillna(0) > 0
        if "RV" in name and "momentum" in name:
            mask = mask & (big.get("rel_volume", 0).fillna(0) >= 1.5)
        if "EMA20>EMA50" in name:
            mask = mask & (big.get("ema20_ema50", 0).fillna(0) > 0)
        if "mom" in name and "RV" not in name and "RS" not in name:
            mask = mask & (big.get("return_10", 0).fillna(0) > 0)
        if "mom+RS" in name:
            mask = (big.get("return_10", 0).fillna(0) > 0) & (big.get("rs_10", 0).fillna(0) > 0)
        if "mom+RS+RV" in name:
            mask = (big.get("return_10", 0).fillna(0) > 0) & (big.get("rs_10", 0).fillna(0) > 0) & (big.get("rel_volume", 0).fillna(0) >= 1.5)
        sub = big[mask]
        for h in horizons:
            fcol = f"future_return_{h}"
            if fcol not in sub.columns:
                continue
            exc = f"excess_return_{h}"
            er = sub[exc].mean() * 100 if exc in sub.columns else 0
            print(
                f"  {name}_{h}: n={len(sub)} mean={sub[fcol].mean() * 100:.3f}% wr={(sub[fcol] > 0).mean() * 100:.1f}% excess={er:.3f}bp"
            )

    # --- Continuation vs reversal ---
    print("\n=== Continuation vs Reversal ===")
    for sig in signals[:3]:
        if sig not in big.columns:
            continue
        col = big[sig].dropna()
        strong = big[big[sig] > col.quantile(0.90)]
        weak = big[big[sig] < col.quantile(0.10)]
        for h in (1, 3, 6):
            fcol = f"future_return_{h}"
            if fcol not in strong.columns:
                continue
            sm = strong[fcol].mean() * 100
            wm = weak[fcol].mean() * 100
            print(
                f"  {sig}: top10% fwd={h} mean={sm:.4f}% | bot10% fwd={h} mean={wm:.4f}% | baseline={big[fcol].mean() * 100:.4f}%"
            )

    print(f"\nAll outputs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
