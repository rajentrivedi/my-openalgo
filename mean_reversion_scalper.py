"""
Mean-Reversion Scalper for NSE 5-minute.
Based on V6.2 finding: bottom-decile 10-bar momentum predicts reversal.
Zero-brokerage account assumed; minimal slippage.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

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


def _run(raw, nifty_df, p, lookback=10, ret_pctile=0.10):
    ws = {}
    for s, df in raw.items():
        sig = compute_signals(df, p)
        ret = sig["close"].pct_change(lookback)
        thr = ret.quantile(ret_pctile)
        long_sig = (ret < thr) & (ret.notna()) & (ret < 0)
        short_sig = (ret > ret.quantile(1 - ret_pctile)) & (ret.notna()) & (ret > 0)
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
    n2 = float(d["net_pnl"].sum())
    w = d[d["net_pnl"] > 0]
    lc = d[d["net_pnl"] < 0]
    pf = (
        abs(w["net_pnl"].sum() / lc["net_pnl"].sum())
        if not lc.empty and lc["net_pnl"].sum() != 0
        else (float("inf") if len(w) else 0)
    )
    return {
        "n": len(d),
        "gross": g,
        "cost": c,
        "net": n2,
        "wr": (d["net_pnl"] > 0).mean() * 100,
        "pf": pf,
        "avg_r": float(d["r_multiple"].mean()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", default="2026-01-01")
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

    p = BacktestParams()
    p.max_trades_per_day = 5
    p.max_open_positions = 5
    p.max_portfolio_open_risk_pct = 1.0
    p.use_daily_loss_limit = True
    p.max_daily_loss_pct = 2.0
    p.estimated_cost_pct = 0.0  # zero brokerage
    p.slippage_pct = 0.005  # 0.005% one-way slippage
    p.risk_per_trade_pct = 0.25
    p.target_rr = 1.0  # 1:1 risk/reward (target = stop distance)
    p.use_target = True
    p.use_trailing = False
    p.trail_activation_r = 0.0
    p.intrabar_exit_policy = "CONSERVATIVE"

    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    t1 = all_ts[int(n * 0.50)]
    t2 = all_ts[int(n * 0.70)]
    t3 = all_ts[int(n * 0.85)]
    print(f"Train<{t1} Val<{t2} Test<{t3} Fresh>{t3}")

    tr = {s: d[d.index < t1] for s, d in raw.items() if (d.index < t1).any()}
    va = {
        s: d[(d.index >= t1) & (d.index < t2)]
        for s, d in raw.items()
        if ((d.index >= t1) & (d.index < t2)).any()
    }
    te = {
        s: d[(d.index >= t2) & (d.index < t3)]
        for s, d in raw.items()
        if ((d.index >= t2) & (d.index < t3)).any()
    }
    fr = {s: d[d.index >= t3] for s, d in raw.items() if (d.index >= t3).any()}

    # Target and stop are proportional to ATR: target = 0.5*ATR, stop = 0.3*ATR
    # We set TARGET_RR and ATR_STOP_MULTIPLIER
    p.target_rr = 1.0  # will be overridden per-trade below

    for label, dset, dn, tp in [
        ("Train", tr, None, tr),
        ("Val", va, nifty[nifty.index >= t1] if nifty is not None else None, va),
        ("Test", te, None, te),
        ("Fresh FR", fr, None, fr),
    ]:
        if not dset:
            continue
        _t, eq, _, _ = _run(dset, dn, p)
        st = _stats(_t, eq)
        print(
            f"{label}: n={st['n']} net={st['net']:.0f} wr={st['wr']:.1f}% pf={st['pf']:.3f} avgR={st['avg_r']:.3f}"
        )
        if label == "Test" and _t:
            pd.DataFrame([x.to_row() for x in _t]).to_csv(f"{OUT_DIR}/mr_trades.csv", index=False)
            if not eq.empty:
                eq.to_csv(f"{OUT_DIR}/mr_equity.csv", index=False)

    # Walk-forward
    print("\nWalk-Forward:")
    for w in range(3):
        wf_tr = all_ts[int(n * 0.30 * (1 + w * 0.10))]
        wf_te = all_ts[min(int(n * (0.40 + w * 0.10)), n - 1)]
        wr = {
            s: d[(d.index >= wf_tr) & (d.index < wf_te)]
            for s, d in raw.items()
            if ((d.index >= wf_tr) & (d.index < wf_te)).any()
        }
        if not wr:
            continue
        _tw, eqw, _, _ = _run(wr, None, p)
        sw = _stats(_tw, eqw)
        print(f"  W{w + 1}: n={sw['n']} net={sw['net']:.0f} pf={sw['pf']:.3f} wr={sw['wr']:.1f}%")

    # Cost/slippage stress on test
    print("\nCost Slippage Stress (test):")
    for slip in [0, 0.005, 0.01, 0.015]:
        p2 = BacktestParams(**{**p.__dict__})
        p2.slippage_pct = slip
        _tc, eqc, _, _ = _run(te, None, p2)
        sc = _stats(_tc, eqc)
        print(
            f"  slip={slip}%: n={sc['n']} net={sc['net']:.0f} wr={sc['wr']:.1f}% pf={sc['pf']:.3f}"
        )

    print("\nOutputs in", OUT_DIR)


if __name__ == "__main__":
    main()
