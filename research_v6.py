"""
V6 — Intraday Momentum + Relative Strength + Abnormal Volume + MA Trend Structure
============================================================================
Fundamentally different entry architecture from V4/V5 (no consolidation breakout).

Tests each component INDEPENDENTLY (MA structure, slope, volume, regime),
then the composite hypothesis, then a full-recipe competitor.

Reuses the validated V3 single-portfolio engine; only replaces the entry-signal
logic by overriding long_signal/short_signal columns.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)

from backtest_range_breakout import (
    IST,
    BacktestParams,
    compute_nifty_signals,
    compute_signals,
    load_symbols,
    load_universe_data,
    print_data_quality,
    run_portfolio_backtest,
)

OUT_DIR = "diagnostics"
os.makedirs(OUT_DIR, exist_ok=True)

EMA9, EMA20, EMA50 = 9, 20, 50


def _run(raw, nifty_df, p, mode):
    """Run with a V6 signal mode overriding the engine's signals."""
    ws = {}
    for s, df in raw.items():
        sig = compute_signals(df, p)
        long_sig = sig["long_signal"].astype(bool).copy()
        short_sig = sig["short_signal"].astype(bool).copy()

        if "mom_close_ema20" in mode:
            long_sig = sig["close"] > sig["ema20"]
            short_sig = sig["close"] < sig["ema20"]
        if "ma9_gt20" in mode:
            long_sig = long_sig & (sig["ema9"] > sig["ema20"])
            short_sig = short_sig & (sig["ema9"] < sig["ema20"])
        if "ma20_gt50" in mode:
            long_sig = long_sig & (sig["ema20"] > sig["ema50"])
            short_sig = short_sig & (sig["ema20"] < sig["ema50"])
        if "struct" in mode:
            long_sig = (sig["ema9"] > sig["ema20"]) & (sig["ema20"] > sig["ema50"])
            short_sig = (sig["ema9"] < sig["ema20"]) & (sig["ema20"] < sig["ema50"])
        if "slope" in mode:
            long_sig = long_sig & (sig["ema20_slope"] > 0)
            short_sig = short_sig & (sig["ema20_slope"] < 0)
        if "vol" in mode:
            vol_ok_l = sig["rel_volume"] >= p.relative_volume_multiplier
            long_sig = long_sig & vol_ok_l
            short_sig = short_sig & vol_ok_l

        sig = sig.copy()
        sig["long_signal"] = long_sig
        sig["short_signal"] = short_sig
        ws[s] = sig

    ns = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    t, eq, d, s = run_portfolio_backtest(ws, ns, p)
    return t, eq, d, s


def _stats(t, eq):
    if not t:
        return {"n": 0, "gross": 0, "cost": 0, "net": 0, "wr": 0, "pf": 0, "avg_r": 0}
    d = pd.DataFrame([x.to_row() for x in t])
    g = float(d["gross_pnl"].sum())
    c = float(d["estimated_cost"].sum())
    n = float(d["net_pnl"].sum())
    w = d[d["net_pnl"] > 0]
    lc = d[d["net_pnl"] < 0]
    pf_ = (
        abs(w["net_pnl"].sum() / lc["net_pnl"].sum())
        if not lc.empty and lc["net_pnl"].sum() != 0
        else (float("inf") if len(w) else 0)
    )
    return {
        "n": len(d),
        "gross": g,
        "cost": c,
        "net": n,
        "wr": (d["net_pnl"] > 0).mean() * 100,
        "pf": pf_,
        "avg_r": float(d["r_multiple"].mean()),
    }


def _split(raw, nifty, t1, t2):
    tr = {s: d[d.index < t1] for s, d in raw.items() if (d.index < t1).any()}
    te = {s: d[d.index >= t2] for s, d in raw.items() if (d.index >= t2).any()}
    tn = nifty[nifty.index < t1] if nifty is not None else None
    ten = nifty[nifty.index >= t2] if nifty is not None else None
    return tr, te, tn, ten


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"):
        print("ERROR: OPENALGO_API_KEY")
        sys.exit(1)

    symbols = load_symbols()[: args.sample]
    raw, nifty, quality = load_universe_data(symbols, args.cache, args.start, args.end)
    print_data_quality(quality)
    if not raw:
        sys.exit(1)

    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    t1 = all_ts[int(n * 0.60)]
    t2 = all_ts[int(n * 0.75)]
    print(f"Train<{t1} Test>{t2}")
    tr, te, tn, ten = _split(raw, nifty, t1, t2)

    base = BacktestParams()
    base.relative_volume_multiplier = 1.5
    base.ema9_len, base.ema20_len, base.ema50_len = EMA9, EMA20, EMA50

    print("\n=== Single-component attribution (each IN ADDITION to close_vs_ema20) ===")
    tests = [
        ("A close>EMA20 (bare momentum)", ["mom_close_ema20"]),
        ("B + EMA9>EMA20", ["mom_close_ema20", "ma9_gt20"]),
        ("C + EMA20>EMA50", ["mom_close_ema20", "ma20_gt50"]),
        ("D + EMA20 slope>0", ["mom_close_ema20", "slope"]),
        ("E EMA9>EMA20>EMA50 (full struct)", ["mom_close_ema20", "ma9_gt20", "ma20_gt50"]),
        ("F struct + slope + vol", ["mom_close_ema20", "ma9_gt20", "ma20_gt50", "slope", "vol"]),
    ]
    rows = []
    for label, mode in tests:
        _t, _eq, _, _ = _run(tr, tn, base, mode)
        s_tr = _stats(_t, _eq)
        _t2, _eq2, _, _ = _run(te, ten, base, mode)
        s_te = _stats(_t2, _eq2)
        rows.append(
            {
                "config": label,
                "tr_n": s_tr["n"],
                "tr_net": round(s_tr["net"]),
                "tr_pf": s_tr["pf"],
                "te_n": s_te["n"],
                "te_net": round(s_te["net"]),
                "te_pf": round(s_te["pf"], 3),
                "te_wr": round(s_te["wr"], 1),
            }
        )
        print(
            f"  {label:<32} tr:{s_tr['net']:>6}(pf {s_tr['pf']:.3f})  te:{s_te['net']:>6}(pf {s_te['pf']:.3f})"
        )

    # Final full recipe with regime filter (NIFTY alignment) on test+full
    print("\n=== Full V6 recipe: struct + slope + vol + NIFTY regime ===")
    full_mode = ["mom_close_ema20", "ma9_gt20", "ma20_gt50", "slope", "vol"]
    _base = BacktestParams(**{**base.__dict__})
    _base.use_market_filter = True
    _t, _eq, _, _ = _run(tr, tn, _base, full_mode)
    s_tr = _stats(_t, _eq)
    _t2, _eq2, _, _ = _run(te, ten, _base, full_mode)
    s_te = _stats(_t2, _eq2)
    rows.append(
        {
            "config": "FULL recipe + regime",
            "tr_n": s_tr["n"],
            "tr_net": round(s_tr["net"]),
            "tr_pf": s_tr["pf"],
            "te_n": s_te["n"],
            "te_net": round(s_te["net"]),
            "te_pf": round(s_te["pf"], 3),
            "te_wr": round(s_te["wr"], 1),
        }
    )
    print(
        f"  full+regime: tr:{s_tr['net']:>6}(pf {s_tr['pf']:.3f})  te:{s_te['net']:>6}(pf {s_te['pf']:.3f})"
    )

    resource = pd.DataFrame(rows)
    resource.to_csv(f"{OUT_DIR}/v6_attribution.csv", index=False)

    print("\n=== Equity/Daily for full recipe (test period) ===")
    _eq2.to_csv(f"{OUT_DIR}/v6_equity.csv", index=False)
    if not _t:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v6_daily.csv", index=False)

    report = f"""# V6 Research Report — Intraday Momentum + MA Trend + Abnormal Volume

## Attribution (train/test)
{resource.to_string()}
"""
    report += f"""
## Test-period equity rows: {len(_eq2)}
"""
    verdict = "NO ROBUST EDGE"
    if s_te["n"] > 0:
        if s_te["pf"] > 1.2:
            verdict = "PROMISING — NEED MORE DATA"
    report += f"## Final Recommendation: **{verdict}**\n"

    with open(f"{OUT_DIR}/V6_RESEARCH_REPORT.md", "w") as f:
        f.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
