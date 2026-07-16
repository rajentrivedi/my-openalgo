"""
Regime-Three: Three-mode intraday strategy for NSE.
Mode 1: Momentum continuation (trending NIFTY)
Mode 2: Mean reversion (ranging NIFTY)
Mode 3: No trade (neutral/low-volatility NIFTY)

Single portfolio. V3 engine. Regime determined per-day from NIFTY.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

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
    to_ist,
)

OUT_DIR = "diagnostics"; os.makedirs(OUT_DIR, exist_ok=True)

def _run_mode(raw, nifty_df, p, mode: int):
    """Run backtest with mode-specific signal overrides."""
    ws = {}
    for s, df in raw.items():
        sig = compute_signals(df, p)
        sig = sig.copy()
        if mode == 1:
            # Momentum continuation: struct + slope + volume
            sig["long_signal"] = (sig["close"] > sig["ema20"]) & (sig["ema9"] > sig["ema20"]) & (sig["ema20"] > sig["ema50"]) & (sig["ema20_slope"] > 0) & (sig["rel_volume"] >= 1.5)
            sig["short_signal"] = (sig["close"] < sig["ema20"]) & (sig["ema9"] < sig["ema20"]) & (sig["ema20"] < sig["ema50"]) & (sig["ema20_slope"] < 0) & (sig["rel_volume"] >= 1.5)
        elif mode == 2:
            # Mean reversion: fade extreme 20-bar return
            ret = sig["close"].pct_change(20)
            thr_bot = ret.quantile(0.10)
            thr_top = ret.quantile(0.90)
            sig["long_signal"] = (ret < thr_bot) & ret.notna()
            sig["short_signal"] = (ret > thr_top) & ret.notna()
        else:
            sig["long_signal"] = False
            sig["short_signal"] = False
        ws[s] = sig
    ns = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    t, eq, d, s = run_portfolio_backtest(ws, ns, p)
    return t, eq, d, s

def _stats(t, eq):
    if not t: return {"n":0,"gross":0,"cost":0,"net":0,"wr":0,"pf":0,"avg_r":0}
    d = pd.DataFrame([x.to_row() for x in t])
    g=float(d["gross_pnl"].sum()); c=float(d["estimated_cost"].sum()); n2=float(d["net_pnl"].sum())
    w=d[d["net_pnl"]>0]; lc=d[d["net_pnl"]<0]
    pf=abs(w["net_pnl"].sum()/lc["net_pnl"].sum()) if not lc.empty and lc["net_pnl"].sum()!=0 else (float("inf") if len(w) else 0)
    return {"n":len(d),"gross":g,"cost":c,"net":n2,"wr":(d["net_pnl"]>0).mean()*100,"pf":pf,"avg_r":float(d["r_multiple"].mean())}

def _regime_for_day(ndf: pd.DataFrame, day: str) -> int:
    """Classify a trading day as mode 1, 2, or 3 based on NIFTY data up to that day."""
    d_candles = ndf[ndf.index < pd.Timestamp(day, tz=IST) + timedelta(days=1)] if ndf is not None else None
    if d_candles is None or len(d_candles) < 50:
        return 2  # default to mean reversion
    last = d_candles.iloc[-1]
    ema20 = float(last.get("ema20", 0))
    ema50 = float(last.get("ema50", 0))
    slope = float(last.get("ema20_slope", 0))
    atr = float(last.get("atr_1m", 0.5))
    atr_med = float(d_candles["atr_1m"].median()) if "atr_1m" in d_candles.columns else atr
    ret_20 = (float(last["close"]) / float(d_candles.iloc[-20]["close"]) - 1) if len(d_candles) >= 20 else 0

    trending = ema20 > ema50 and slope > 0
    ranging = abs(ret_20 * 100) < 2.0 or atr < atr_med * 0.8
    if trending and not ranging:
        return 1
    elif ranging:
        return 2
    return 3

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"): print("ERROR: OPENALGO_API_KEY"); sys.exit(1)

    symbols = load_symbols()[:args.sample]
    raw, nifty_raw, quality = load_universe_data(symbols, args.cache, args.start, args.end)
    print_data_quality(quality)
    if not raw: sys.exit(1)

    # Prepare NIFTY with regime features (use 1m available or 5m)
    ndf = nifty_raw.copy() if nifty_raw is not None else None
    if ndf is not None:
        ndf["close"] = pd.to_numeric(ndf["close"], errors="coerce")
        ndf["ema20"] = ndf["close"].ewm(span=20, adjust=False).mean()
        ndf["ema50"] = ndf["close"].ewm(span=50, adjust=False).mean()
        try:
            from openalgo import ta
            ndf["atr_1m"] = ta.atr(ndf["high"], ndf["low"], ndf["close"], 14)
        except: ndf["atr_1m"] = ndf["high"] - ndf["low"]
        ndf["ema20_slope"] = (ndf["ema20"] - ndf["ema20"].shift(3)) / ndf["atr_1m"].clip(lower=1e-10)

    # Get unique days from stock data
    all_days = set()
    for df in raw.values():
        for ts in df.index:
            d = to_ist(ts).strftime("%Y-%m-%d")
            if "09:20" <= to_ist(ts).strftime("%H:%M") <= "15:20":
                all_days.add(d)
    all_days = sorted(all_days)
    print(f"Trading days: {len(all_days)}")

    # Classify regimes
    regimes = {}
    for d in all_days:
        r = _regime_for_day(ndf, d)
        regimes[d] = r
    reg_counts = {1: 0, 2: 0, 3: 0}
    for r in regimes.values(): reg_counts[r] = reg_counts.get(r, 0) + 1
    print(f"Regime counts: M1(trend)={reg_counts[1]} M2(reversion)={reg_counts[2]} M3(neutral)={reg_counts[3]}")

    # Build per-day partitioned data
    p = BacktestParams()
    p.estimated_cost_pct = 0.0
    p.slippage_pct = 0.005
    p.max_trades_per_day = 5
    p.max_open_positions = 5
    p.max_portfolio_open_risk_pct = 1.0
    p.use_daily_loss_limit = True
    p.max_daily_loss_pct = 2.0
    p.risk_per_trade_pct = 0.25
    p.use_target = True
    p.use_trailing = False
    p.intrabar_exit_policy = "CONSERVATIVE"

    # Run each mode on its days
    all_trades = []
    all_eq_rows = []
    realized_pnl = 0.0
    trade_id = 0

    for d in all_days:
        mode = regimes.get(d, 2)
        if mode == 3:
            continue  # no trade
        # Subset raw to this day
        day_start = pd.Timestamp(d, tz=IST)
        day_end = day_start + timedelta(hours=6, minutes=15)  # 15:30 IST
        day_raw = {}
        for sym, df in raw.items():
            sub = df[(df.index >= day_start) & (df.index <= day_end)]
            if not sub.empty:
                day_raw[sym] = sub
        if not day_raw:
            continue
        # Run mode for this day with carry-over realized_pnl
        t_day, eq_day, _, _ = _run_mode(day_raw, ndf[(ndf.index >= day_start) & (ndf.index <= day_end)] if ndf is not None else None, p, mode)
        if t_day:
            for tr in t_day:
                all_trades.append(tr)
        if not eq_day.empty:
            eq_day["equity_adj"] = eq_day["total_equity"] + (realized_pnl - eq_day["realized_pnl"].iloc[0]) if "realized_pnl" in eq_day.columns else eq_day["total_equity"]
            all_eq_rows.append(eq_day)
        realized_pnl += sum(t.net_pnl for t in t_day) if t_day else 0

    # Results
    print(f"\nTotal trades: {len(all_trades)}")
    if all_trades:
        tdf = pd.DataFrame([t.to_row() for t in all_trades])
        total_net = tdf["net_pnl"].sum()
        total_gross = tdf["gross_pnl"].sum()
        total_cost = tdf["estimated_cost"].sum()
        wr = (tdf["net_pnl"] > 0).mean() * 100
        wins = tdf[tdf["net_pnl"] > 0]; losses = tdf[tdf["net_pnl"] < 0]
        pf = abs(wins["net_pnl"].sum() / losses["net_pnl"].sum()) if not losses.empty and losses["net_pnl"].sum() != 0 else float("inf") if len(wins) else 0
        print(f"Gross: {total_gross:.0f}  Cost: {total_cost:.0f}  Net: {total_net:.0f}")
        print(f"Win rate: {wr:.1f}%  PF: {pf:.3f}")
        print(f"Final equity: {100000+total_net:.0f}")
        print("\nExit reasons:")
        print(tdf["exit_reason"].value_counts().to_string())
        # Per mode
        if "entry_mode" in tdf.columns:
            for m in [1, 2, 3]:
                sub = tdf[tdf["entry_mode"].astype(str).str.contains(f"mode={m}")] if "entry_mode" in tdf.columns else tdf
                # Actually entry_mode isn't stored. Let's check session instead
        print(f"\nLong: n={(tdf['direction']=='LONG').sum()} net={tdf[tdf['direction']=='LONG']['net_pnl'].sum():.0f}")
        print(f"Short: n={(tdf['direction']=='SHORT').sum()} net={tdf[tdf['direction']=='SHORT']['net_pnl'].sum():.0f}")
        tdf.to_csv(f"{OUT_DIR}/regime_three_trades.csv", index=False)
    if all_eq_rows:
        pd.concat(all_eq_rows).to_csv(f"{OUT_DIR}/regime_three_equity.csv", index=False)

    # Mode-by-mode breakdown
    print(f"\nRegime distribution: {dict(reg_counts)}")

if __name__ == "__main__":
    main()
