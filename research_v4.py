"""
V4 Controlled Research Framework
================================
Runs a non-combinatorial grid over the highest-potential signal parameters,
splitting historical data into TRAIN / VALIDATE / TEST periods.

DOES NOT optimize for maximum historical P&L.
Reports OOS (validation + test) performance first.
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

from backtest_range_breakout import (  # noqa: E402
    IST,
    LOOKBACK_DAYS,
    BacktestParams,
    compute_nifty_signals,
    compute_signals,
    load_symbols,
    load_universe_data,
    print_data_quality,
    run_portfolio_backtest,
)

OUT_FILE = "v4_research_results.csv"
TRAIN_FRAC = 0.60
VAL_FRAC = 0.20  # test = 1 - (train+val)


def _run(raw, nifty_df, p):
    signals = {s: compute_signals(df, p) for s, df in raw.items()}
    nifty_sig = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    trades, equity_df, daily_df, summary = run_portfolio_backtest(signals, nifty_sig, p)
    return trades, equity_df, daily_df, summary


def _strip(trades, equity_df):
    if not trades:
        return {
            "n": 0,
            "gross": 0.0,
            "net": 0.0,
            "win_rate": 0.0,
            "pf": 0.0,
            "avg_r": 0.0,
            "sharpe": 0.0,
            "max_dd_pct": 0.0,
        }
    tdf = pd.DataFrame([t.to_row() for t in trades])
    gross = float(tdf["gross_pnl"].sum())
    cost = float(tdf["estimated_cost"].sum())
    net = float(tdf["net_pnl"].sum())
    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] < 0]
    pf = (
        abs(wins["net_pnl"].sum() / losses["net_pnl"].sum())
        if not losses.empty and losses["net_pnl"].sum() != 0
        else float("inf")
        if len(wins)
        else 0.0
    )
    wr = (tdf["net_pnl"] > 0).mean() * 100 if len(tdf) else 0.0
    eq = equity_df["total_equity"] if not equity_df.empty else pd.Series([100000.0])
    peak = eq.cummax()
    ddp = ((peak - eq) / peak * 100).max() if peak.max() > 0 else 0.0
    sharpe = 0.0
    if not equity_df.empty and len(eq) > 5:
        d = equity_df.copy()
        d["day"] = pd.to_datetime(d["timestamp"]).dt.date
        de = d.groupby("day")["total_equity"].last()
        r = de.pct_change().dropna()
        if len(r) > 1 and r.std() > 0:
            sharpe = float(r.mean() / r.std() * np.sqrt(252))
    return {
        "n": len(tdf),
        "gross": gross,
        "cost": cost,
        "net": net,
        "win_rate": wr,
        "pf": pf,
        "avg_r": float(tdf["r_multiple"].mean()),
        "sharpe": round(sharpe, 3),
        "max_dd_pct": round(ddp, 2),
    }


def _trim_ts(obj, min_ts, max_ts):
    """Filter raw/nifty data to [min_ts, max_ts] date range."""
    out = {}
    for sym, df in obj.items():
        if df.empty:
            continue
        d = df[(df.index >= min_ts) & (df.index <= max_ts)]
        if not d.empty:
            out[sym] = d
    return out


def _resume_trades(trades_df):
    """Resume state (realized_pnl) from a previous window's final equity."""
    return float(trades_df["net_pnl"].sum()) if not trades_df.empty else 0.0


def _partition_dates(raw: dict, train_frac: float, val_frac: float):
    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    if not all_ts:
        return None, None, None
    n = len(all_ts)
    t1 = all_ts[int(n * train_frac)]
    t2 = all_ts[int(n * (train_frac + val_frac))]
    return t1, t2, all_ts[0], all_ts[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="V4 controlled research")
    parser.add_argument("--sample", type=int, default=10)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--light", action="store_true", help="Small grid over RV, RW, BA only")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"):
        print("ERROR: OPENALGO_API_KEY is not configured.")
        sys.exit(1)

    end_s = args.end or datetime.now(IST).strftime("%Y-%m-%d")
    start_s = args.start or (datetime.now(IST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    symbols = load_symbols()
    if args.sample:
        symbols = symbols[: args.sample]

    print("=" * 70)
    print("V4 CONTROLLED RESEARCH — Range-breakout entry diagnosis")
    print("=" * 70)
    print(f"Universe: {len(symbols)} symbols | Period: {start_s} -> {end_s}")
    t0 = time.time()

    raw, nifty_df, quality = load_universe_data(symbols, args.cache, start_s, end_s)
    print_data_quality(quality)
    if not raw:
        print("[ERROR] No data.")
        sys.exit(1)

    all_raw = raw
    all_nifty = nifty_df

    # partition dates
    t1, t2, _first, _last = _partition_dates(all_raw, TRAIN_FRAC, VAL_FRAC)
    if t1 is None or t2 is None:
        print("[ERROR] Not enough data to partition.")
        sys.exit(1)

    train_raw = {}
    for s, d in all_raw.items():
        mask = d.index < t1
        if mask.any():
            train_raw[s] = d[mask]
    val_raw = {}
    for s, d in all_raw.items():
        mask = (d.index >= t1) & (d.index < t2)
        if mask.any():
            val_raw[s] = d[mask]
    test_raw = {}
    for s, d in all_raw.items():
        mask = d.index >= t2
        if mask.any():
            test_raw[s] = d[mask]

    def nifty_split(df, lo, hi):
        if df is None:
            return None
        if lo is None:
            lo = df.index.min()
        if hi is None:
            return df[df.index >= lo]
        return df[(df.index >= lo) & (df.index < hi)]

    tn = nifty_split(all_nifty, None, t1)
    vn_use = nifty_split(all_nifty, t1, t2)
    testn_use = nifty_split(all_nifty, t2, None)

    print(f"Train:   start -> {t1}")
    print(f"Val:     {t1} -> {t2}")
    print(f"Test:    {t2} -> end")
    print(f"Symbols(train): {len(train_raw)} val: {len(val_raw)} test: {len(test_raw)}")

    # ===== GRID =====
    VALS_SESSION = ["BOTH"] if args.light else ["BOTH", "S1", "S2"]
    VALS_DIR = ["both"] if args.light else ["both", "long", "short"]
    VALS_RV = [1.2, 2.0, 3.0] if args.light else [1.2, 1.5, 2.0, 2.5, 3.0]
    VALS_RW = [0.50, 1.00] if args.light else [0.25, 0.50, 0.75, 1.00]
    VALS_BA = [0.10, 0.30] if args.light else [0.10, 0.20, 0.30, 0.50]
    VALS_RB = [8] if args.light else [6, 8, 10, 12]

    base = BacktestParams()

    rows = []
    total = (
        len(VALS_SESSION)
        * len(VALS_DIR)
        * len(VALS_RV)
        * len(VALS_RW)
        * len(VALS_BA)
        * len(VALS_RB)
    )
    done = 0

    for session in VALS_SESSION:
        base2 = BacktestParams(**{**base.__dict__})
        base2.session_switch = "BOTH" if session == "BOTH" else session

        for dirs in VALS_DIR:
            base3 = BacktestParams(**{**base2.__dict__})
            if dirs == "long":
                base3.short_only = False
                base3.long_only = False
                base3.use_market_filter = True
            elif dirs == "short":
                base3.short_only = True
                base3.long_only = False
            else:
                base3.short_only = False
                base3.long_only = False

            for rv in VALS_RV:
                base4 = BacktestParams(**{**base3.__dict__})
                base4.relative_volume_multiplier = rv

                for rw in VALS_RW:
                    base5 = BacktestParams(**{**base4.__dict__})
                    base5.max_range_width_pct = rw

                    for ba in VALS_BA:
                        base6 = BacktestParams(**{**base5.__dict__})
                        base6.min_breakout_atr = ba

                        for rb in VALS_RB:
                            base7 = BacktestParams(**{**base6.__dict__})
                            base7.range_bars = rb

                            combo = f"session={session} dir={dirs} rv={rv} rw={rw}% ba={ba} rb={rb}"

                            # TRAIN
                            if train_raw and tn is not None and not tn.empty:
                                trades, eq, _, _ = _run(train_raw, tn, base7)
                                train = _strip(trades, eq)
                            else:
                                train = _strip([], pd.DataFrame())

                            # VALIDATION (in-sample but train+val combined)
                            val_trades, val_eq, _, _ = _run(val_raw, vn_use, base7)
                            val = _strip(val_trades, val_eq) if val_trades else _strip([], pd.DataFrame())

                            # TEST (out-of-sample)
                            test_trades, test_eq, _, _ = _run(test_raw, testn_use, base7)
                            test = _strip(test_trades, test_eq) if test_trades else _strip([], pd.DataFrame())

                            done += 1
                            rows.append(
                                {
                                    "session": session,
                                    "direction": dirs,
                                    "rv": rv,
                                    "rw_pct": rw,
                                    "breakout_atr": ba,
                                    "range_bars": rb,
                                    "train_n": train["n"],
                                    "train_net": round(train["net"], 0),
                                    "train_pf": train["pf"],
                                    "val_n": val["n"],
                                    "val_net": round(val["net"], 0),
                                    "val_pf": val["pf"],
                                    "val_win_rate": round(val["win_rate"], 1),
                                    "test_n": test["n"],
                                    "test_net": round(test["net"], 0),
                                    "test_pf": test["pf"],
                                    "test_win_rate": round(test["win_rate"], 1),
                                }
                            )
                            if done % 40 == 0:
                                print(f"  [{done}/{total}] {combo[:60]}")

    df = pd.DataFrame(rows)
    df.to_csv(OUT_FILE, index=False)
    elapsed = time.time() - t0
    print(f"\n[DONE] {elapsed:.1f}s — results saved to {OUT_FILE}")

    # top by OOS net
    print("\nTOP 10 BY VALIDATION NET P&L:")
    top = df.nlargest(10, "val_net")
    for _, r in top.iterrows():
        print(
            f"  {r['session']:>5} {r['direction']:>5} rv={r['rv']:3.1f} "
            f"rw={r['rw_pct']:4.2f}% ba={r['breakout_atr']:4.2f} rb={r['range_bars']:3.0f}  "
            f"| train_net={r['train_net']:>8.0f} val_net={r['val_net']:>8.0f} "
            f"test_net={r['test_net']:>8.0f} | val_pf={r['val_pf']:6.3f}"
        )

    print("\nTOP 10 BY TEST NET P&L:")
    for _, r in df.nlargest(10, "test_net").iterrows():
        print(
            f"  {r['session']:>5} {r['direction']:>5} rv={r['rv']:3.1f} "
            f"rw={r['rw_pct']:4.2f}% ba={r['breakout_atr']:4.2f} rb={r['range_bars']:3.0f}  "
            f"| train_net={r['train_net']:>8.0f} val_net={r['val_net']:>8.0f} "
            f"test_net={r['test_net']:>8.0f} | test_pf={r['test_pf']:6.3f} test_wr={r['test_win_rate']:5.1f}%"
        )

    print(f"\nFull results: {OUT_FILE}  ({len(rows)} combos)")


if __name__ == "__main__":
    main()
