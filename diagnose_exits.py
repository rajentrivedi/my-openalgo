"""
V3 Exit Diagnostic
==================
Diagnoses whether the trailing stop is destroying the breakout edge.

Runs controlled experiments (NOT parameter optimization):
  - trailing ON vs OFF vs profit-activated trailing
  - trailing-only vs target-only
  - trailing multiplier sweep (isolated)
  - cost sensitivity
  - slippage sensitivity
  - MFE / MAE excursion analysis
  - time-to-profit analysis
  - holding-time / session / long-short / bucket analysis

Outputs:
  v3_exit_diagnostic_report.txt
  trailing_diagnostic.csv
  trade_excursion_analysis.csv
  cost_sensitivity.csv
  slippage_sensitivity.csv

Usage:
    uv run python diagnose_exits.py --sample 10
    uv run python diagnose_exits.py --sample 10 --cache
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

REPORT_FILE = "v3_exit_diagnostic_report.txt"


def _run(raw, nifty_df, p: BacktestParams):
    signals = {s: compute_signals(df, p) for s, df in raw.items()}
    nifty_sig = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    trades, equity_df, daily_df, summary = run_portfolio_backtest(signals, nifty_sig, p)
    return trades, equity_df, daily_df, summary


def _stats(trades, equity_df) -> dict:
    if not trades:
        return {"trades": 0}
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
    )
    eq = equity_df["total_equity"] if not equity_df.empty else pd.Series([100000.0])
    peak = eq.cummax()
    dd = peak - eq
    ddp = (dd / peak * 100) if peak.max() > 0 else dd * 0
    daily_eq = equity_df.copy()
    if not daily_eq.empty:
        daily_eq["day"] = pd.to_datetime(daily_eq["timestamp"]).dt.date
        day_eq = daily_eq.groupby("day")["total_equity"].last()
        rets = day_eq.pct_change().dropna()
        sharpe = (
            float(rets.mean() / rets.std() * np.sqrt(252))
            if len(rets) > 1 and rets.std() > 0
            else 0.0
        )
    else:
        sharpe = 0.0
    exits = tdf["exit_reason"].value_counts().to_dict()
    return {
        "trades": len(tdf),
        "win_rate": float((tdf["net_pnl"] > 0).mean() * 100),
        "gross_pnl": gross,
        "cost": cost,
        "net_pnl": net,
        "profit_factor": float(pf),
        "avg_r": float(tdf["r_multiple"].mean()),
        "max_dd": float(dd.max()),
        "max_dd_pct": float(ddp.max()),
        "sharpe": sharpe,
        "TARGET": int(exits.get("TARGET", 0)),
        "STOP_LOSS": int(exits.get("STOP_LOSS", 0)),
        "TRAILING_STOP": int(exits.get("TRAILING_STOP", 0)),
        "EOD_SQUARE_OFF": int(exits.get("EOD_SQUARE_OFF", 0)),
        "DAILY_LOSS_LIMIT": int(exits.get("DAILY_LOSS_LIMIT", 0)),
    }


def _scenario(name: str, raw, nifty_df, base: BacktestParams, **overrides) -> dict:
    p = BacktestParams(**{**base.__dict__})
    for k, v in overrides.items():
        setattr(p, k, v)
    trades, equity_df, _daily, _summ = _run(raw, nifty_df, p)
    s = _stats(trades, equity_df)
    s["scenario"] = name
    s["trades_obj"] = trades
    print(
        f"[scn] {name:38s} trades={s.get('trades', 0):4d} "
        f"net={s.get('net_pnl', 0):9.2f} win%={s.get('win_rate', 0):5.1f} "
        f"pf={s.get('profit_factor', 0):5.2f} target={s.get('TARGET', 0):3d} "
        f"stop={s.get('STOP_LOSS', 0):3d} trail={s.get('TRAILING_STOP', 0):3d} "
        f"eod={s.get('EOD_SQUARE_OFF', 0):3d}"
    )
    return s


def main() -> None:
    parser = argparse.ArgumentParser(description="V3 exit diagnostic")
    parser.add_argument("--sample", type=int, default=10)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"):
        print("ERROR: OPENALGO_API_KEY is not configured.")
        sys.exit(1)

    end = args.end or datetime.now(IST).strftime("%Y-%m-%d")
    start = args.start or (datetime.now(IST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    symbols = load_symbols()
    if args.sample:
        symbols = symbols[: args.sample]

    print("=" * 70)
    print("V3 EXIT DIAGNOSTIC — trailing stop vs edge")
    print("=" * 70)
    print(f"Universe: {len(symbols)} symbols | Period: {start} -> {end}")
    print("=" * 70)

    t0 = time.time()
    raw, nifty_df, quality = load_universe_data(symbols, args.cache, start, end)
    print_data_quality(quality)
    if not raw:
        print("[ERROR] No data loaded.")
        sys.exit(1)

    base = BacktestParams()
    report_lines: list[str] = []
    report_lines.append("V3 EXIT DIAGNOSTIC REPORT")
    report_lines.append("=" * 70)
    report_lines.append(f"Universe: {len(symbols)} symbols")
    report_lines.append(f"Period: {start} -> {end}")
    report_lines.append("")

    # ---------------------------------------------------------------
    # §2/§4/§5  Trailing ON / OFF / activation / target-only
    # ---------------------------------------------------------------
    print("\n--- §4/§5 Exit architecture ---")
    s_off = _scenario("T1 target-only (trailing OFF)", raw, nifty_df, base, use_trailing=False)
    s_on = _scenario("T2 trailing ON immediate (0.75)", raw, nifty_df, base, use_trailing=True)
    s_act = _scenario(
        "T3 trailing ON activate +0.5R",
        raw,
        nifty_df,
        base,
        use_trailing=True,
        trail_activation_r=0.5,
    )
    s_trail_only = _scenario(
        "T4 trailing-only (target OFF)", raw, nifty_df, base, use_target=False, use_trailing=True
    )

    report_lines.append("EXIT ARCHITECTURE COMPARISON")
    report_lines.append("-" * 70)
    for s in (s_off, s_on, s_act, s_trail_only):
        report_lines.append(
            f"{s['scenario']:38s} trades={s.get('trades', 0):4d} "
            f"net={s.get('net_pnl', 0):9.2f} win%={s.get('win_rate', 0):5.1f} "
            f"pf={s.get('profit_factor', 0):5.2f} avgR={s.get('avg_r', 0):6.3f} "
            f"target={s.get('TARGET', 0):3d} stop={s.get('STOP_LOSS', 0):3d} "
            f"trail={s.get('TRAILING_STOP', 0):3d} eod={s.get('EOD_SQUARE_OFF', 0):3d}"
        )
    report_lines.append("")

    # ---------------------------------------------------------------
    # §3  Trailing multiplier sweep (isolated)
    # ---------------------------------------------------------------
    print("\n--- §3 Trailing multiplier sweep ---")
    trail_rows = []
    for mult in (0.50, 0.75, 1.00, 1.25, 1.50):
        s = _scenario(
            f"TRAIL_ATR_MULTIPLIER={mult}", raw, nifty_df, base, trail_atr_multiplier=mult
        )
        trail_rows.append(
            {
                "trail_atr_multiplier": mult,
                "trades": s.get("trades", 0),
                "win_rate": round(s.get("win_rate", 0), 2),
                "gross_pnl": round(s.get("gross_pnl", 0), 2),
                "cost": round(s.get("cost", 0), 2),
                "net_pnl": round(s.get("net_pnl", 0), 2),
                "profit_factor": round(s.get("profit_factor", 0), 3),
                "avg_r": round(s.get("avg_r", 0), 3),
                "max_drawdown": round(s.get("max_dd", 0), 2),
                "sharpe": round(s.get("sharpe", 0), 3),
                "TARGET": s.get("TARGET", 0),
                "STOP_LOSS": s.get("STOP_LOSS", 0),
                "TRAILING_STOP": s.get("TRAILING_STOP", 0),
                "EOD_SQUARE_OFF": s.get("EOD_SQUARE_OFF", 0),
            }
        )
    trail_df = pd.DataFrame(trail_rows)
    trail_df.to_csv("trailing_diagnostic.csv", index=False)
    report_lines.append("TRAILING MULTIPLIER SWEEP (trailing on, target on)")
    report_lines.append("-" * 70)
    report_lines.append(trail_df.to_string(index=False))
    report_lines.append("")

    # ---------------------------------------------------------------
    # §15/§16 cost + slippage sensitivity (use trailing ON config)
    # ---------------------------------------------------------------
    print("\n--- §15/§16 Cost & slippage sensitivity ---")
    cost_rows = []
    for c in (0.00, 0.01, 0.02, 0.03, 0.05):
        s = _scenario(f"COST={c}%", raw, nifty_df, base, estimated_cost_pct=c)
        cost_rows.append(
            {
                "cost_pct": c,
                "gross_pnl": round(s.get("gross_pnl", 0), 2),
                "cost": round(s.get("cost", 0), 2),
                "net_pnl": round(s.get("net_pnl", 0), 2),
                "win_rate": round(s.get("win_rate", 0), 2),
                "profit_factor": round(s.get("profit_factor", 0), 3),
            }
        )
    cost_df = pd.DataFrame(cost_rows)
    cost_df.to_csv("cost_sensitivity.csv", index=False)

    slip_rows = []
    for sl in (0.00, 0.01, 0.02, 0.03):
        s = _scenario(f"SLIP={sl}%", raw, nifty_df, base, slippage_pct=sl)
        slip_rows.append(
            {
                "slippage_pct": sl,
                "gross_pnl": round(s.get("gross_pnl", 0), 2),
                "net_pnl": round(s.get("net_pnl", 0), 2),
                "win_rate": round(s.get("win_rate", 0), 2),
                "profit_factor": round(s.get("profit_factor", 0), 3),
            }
        )
    slip_df = pd.DataFrame(slip_rows)
    slip_df.to_csv("slippage_sensitivity.csv", index=False)

    report_lines.append("COST SENSITIVITY (0.02% slippage, trailing on)")
    report_lines.append("-" * 70)
    report_lines.append(cost_df.to_string(index=False))
    report_lines.append("")
    report_lines.append("SLIPPAGE SENSITIVITY (0.03% cost, trailing on)")
    report_lines.append("-" * 70)
    report_lines.append(slip_df.to_string(index=False))
    report_lines.append("")

    # ---------------------------------------------------------------
    # Excursion / time-to-profit analysis — use the trailing ON run
    # ---------------------------------------------------------------
    print("\n--- §8/§9/§10 Excursion & time-to-profit ---")
    trades = s_on.get("trades_obj", [])
    if trades:
        tdf = pd.DataFrame([t.to_row() for t in trades])
        exc = tdf[
            [
                "trade_id",
                "symbol",
                "direction",
                "entry_time",
                "exit_time",
                "exit_reason",
                "risk_amount",
                "mfe",
                "mfe_r",
                "mae",
                "mae_r",
                "r_multiple",
                "duration_minutes",
                "bars_held",
                "ttp_025",
                "ttp_050",
                "ttp_075",
                "ttp_100",
                "ttp_150",
            ]
        ].rename(columns={"risk_amount": "initial_risk", "r_multiple": "final_R"})
        exc.to_csv("trade_excursion_analysis.csv", index=False)

        # §8 R-multiple distribution
        bins = [-np.inf, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, np.inf]
        labels = [
            "<-1.0",
            "-1.0..-0.5",
            "-0.5..0",
            "0..0.5",
            "0.5..1.0",
            "1.0..1.5",
            "1.5..2.0",
            ">2.0",
        ]
        tdf["r_bucket"] = pd.cut(tdf["r_multiple"], bins=bins, labels=labels)
        r_dist = (
            tdf.groupby("r_bucket", observed=False)
            .agg(
                trades=("r_multiple", "count"),
                gross_pnl=("gross_pnl", "sum"),
                net_pnl=("net_pnl", "sum"),
            )
            .reset_index()
        )
        r_dist["pct"] = (r_dist["trades"] / len(tdf) * 100).round(2)
        report_lines.append("R-MULTIPLE DISTRIBUTION (trailing on)")
        report_lines.append("-" * 70)
        report_lines.append(r_dist.to_string(index=False))
        report_lines.append("")

        # §10 time-to-profit
        ttp_levels = [
            ("+0.25R", "ttp_025"),
            ("+0.50R", "ttp_050"),
            ("+0.75R", "ttp_075"),
            ("+1.00R", "ttp_100"),
            ("+1.50R", "ttp_150"),
        ]
        report_lines.append("TIME-TO-PROFIT (bars of 5m; -1 = never reached)")
        report_lines.append("-" * 70)
        for lbl, col in ttp_levels:
            reached = tdf[tdf[col] >= 0]
            pct = len(reached) / len(tdf) * 100 if len(tdf) else 0
            med = reached[col].median() if not reached.empty else float("nan")
            avg = reached[col].mean() if not reached.empty else float("nan")
            report_lines.append(
                f"  {lbl:7s} reached {pct:5.1f}%  median={med:5.1f} bars  mean={avg:5.1f} bars  "
                f"(n={len(reached)})"
            )
        report_lines.append("")

        # Profit give-back: trades that reached +1R but exited below +0.5R
        reached_1r = tdf[tdf["ttp_100"] >= 0]
        gave_back = reached_1r[reached_1r["r_multiple"] < 0.5]
        report_lines.append("PROFIT GIVE-BACK")
        report_lines.append("-" * 70)
        report_lines.append(
            f"  Trades reaching +1R:      {len(reached_1r)} / {len(tdf)} "
            f"({len(reached_1r) / len(tdf) * 100:.1f}%)"
        )
        report_lines.append(
            f"  Of those, exited < +0.5R: {len(gave_back)} "
            f"({len(gave_back) / len(reached_1r) * 100:.1f}% of +1R trades)"
        )
        if not gave_back.empty:
            report_lines.append(
                f"  Avg peak MFE of those:    {gave_back['mfe_r'].mean():.2f}R, "
                f"avg final: {gave_back['r_multiple'].mean():.2f}R"
            )
        report_lines.append("")

        # §11 holding-time buckets
        hold_bins = [0, 5, 10, 15, 30, 60, np.inf]
        hold_labels = ["0-5", "5-10", "10-15", "15-30", "30-60", "60+"]
        tdf["hold_bucket"] = pd.cut(tdf["duration_minutes"], bins=hold_bins, labels=hold_labels)
        hold = (
            tdf.groupby("hold_bucket", observed=False)
            .agg(
                trades=("net_pnl", "count"),
                win_rate=("net_pnl", lambda x: (x > 0).mean() * 100),
                net_pnl=("net_pnl", "sum"),
                avg_r=("r_multiple", "mean"),
            )
            .reset_index()
        )
        report_lines.append("HOLDING-TIME BUCKETS (minutes)")
        report_lines.append("-" * 70)
        report_lines.append(hold.to_string(index=False))
        report_lines.append("")

        # §12 entry-quality buckets
        def bucket(col, edges, label):
            sub = tdf.copy()
            out = []
            for i in range(len(edges) - 1):
                seg = sub[(sub[col] >= edges[i]) & (sub[col] < edges[i + 1])]
                if seg.empty:
                    continue
                out.append(
                    f"  {edges[i]:.2f}-{edges[i + 1]:.2f}: n={len(seg):3d} "
                    f"win%={(seg['net_pnl'] > 0).mean() * 100:5.1f} net={seg['net_pnl'].sum():8.2f} "
                    f"avgR={seg['r_multiple'].mean():6.3f}"
                )
            seg = sub[sub[col] >= edges[-1]]
            if not seg.empty:
                out.append(
                    f"  >{edges[-1]:.2f}: n={len(seg):3d} win%={(seg['net_pnl'] > 0).mean() * 100:5.1f} "
                    f"net={seg['net_pnl'].sum():8.2f} avgR={seg['r_multiple'].mean():6.3f}"
                )
            return [f"{label} BUCKETS", "-" * 70, *out, ""]

        report_lines += bucket("relative_volume", [1.20, 1.50, 2.00, 3.00], "RELATIVE VOLUME")
        report_lines += bucket("range_width_pct", [0.0, 0.50, 0.75, 1.00], "RANGE WIDTH %")

        # §13 session
        report_lines.append("SESSION ANALYSIS")
        report_lines.append("-" * 70)
        for s_label in ("S1", "S2"):
            sub = tdf[tdf["session"] == s_label]
            if sub.empty:
                continue
            pf = (
                abs(
                    sub[sub["net_pnl"] > 0]["net_pnl"].sum()
                    / sub[sub["net_pnl"] < 0]["net_pnl"].sum()
                )
                if sub[sub["net_pnl"] < 0]["net_pnl"].sum() != 0
                else float("inf")
            )
            report_lines.append(
                f"  {s_label}: trades={len(sub):3d} win%={(sub['net_pnl'] > 0).mean() * 100:5.1f} "
                f"gross={sub['gross_pnl'].sum():8.2f} net={sub['net_pnl'].sum():8.2f} "
                f"avgR={sub['r_multiple'].mean():6.3f} pf={pf:.2f}"
            )
        report_lines.append("")

        # §14 long/short
        report_lines.append("LONG vs SHORT")
        report_lines.append("-" * 70)
        for side in ("LONG", "SHORT"):
            sub = tdf[tdf["direction"] == side]
            if sub.empty:
                continue
            pf = (
                abs(
                    sub[sub["net_pnl"] > 0]["net_pnl"].sum()
                    / sub[sub["net_pnl"] < 0]["net_pnl"].sum()
                )
                if sub[sub["net_pnl"] < 0]["net_pnl"].sum() != 0
                else float("inf")
            )
            report_lines.append(
                f"  {side}: trades={len(sub):3d} win%={(sub['net_pnl'] > 0).mean() * 100:5.1f} "
                f"gross={sub['gross_pnl'].sum():8.2f} net={sub['net_pnl'].sum():8.2f} "
                f"avgR={sub['r_multiple'].mean():6.3f} pf={pf:.2f} "
                f"avgdur={sub['duration_minutes'].mean():5.1f}m"
            )
        report_lines.append("")
    else:
        report_lines.append("No trades generated — excursion analysis skipped.")
        report_lines.append("")

    # ---------------------------------------------------------------
    # §17 classification
    # ---------------------------------------------------------------
    report_lines.append("=" * 70)
    report_lines.append("CLASSIFICATION")
    report_lines.append("=" * 70)
    if trades:
        tdf = pd.DataFrame([t.to_row() for t in trades])
        gross_before_cost = float(tdf["gross_pnl"].sum())
        cost_total = float(tdf["estimated_cost"].sum())
        reached_05 = float((tdf["ttp_050"] >= 0).mean() * 100)
        reached_10 = float((tdf["ttp_100"] >= 0).mean() * 100)
        reached_15 = float((tdf["ttp_150"] >= 0).mean() * 100)
        report_lines.append(f"Target-only gross P&L before cost:  {s_off.get('gross_pnl', 0):.2f}")
        report_lines.append(
            f"Target-only exits: TARGET={s_off.get('TARGET', 0)} STOP={s_off.get('STOP_LOSS', 0)}"
        )
        report_lines.append(f"Trailing-ON gross P&L before cost:  {gross_before_cost:.2f}")
        report_lines.append(f"Trailing-ON total estimated cost:   {cost_total:.2f}")
        report_lines.append(f"% trades reaching +0.5R: {reached_05:.1f}%")
        report_lines.append(f"% trades reaching +1.0R: {reached_10:.1f}%")
        report_lines.append(f"% trades reaching +1.5R: {reached_15:.1f}%")
        report_lines.append("")
        report_lines.append("Interpretation guide:")
        report_lines.append("  - If few trades reach +0.5R  -> weak entry edge (Category B)")
        report_lines.append(
            "  - If many reach +1R but final R is low -> trailing gives profit back (A)"
        )
        report_lines.append(
            "  - If gross_before_cost ~ 0 and cost large -> costs kill a small edge (D)"
        )
        report_lines.append(
            "  - target-only materially better than trailing -> trailing destroys edge (A)"
        )

    report = "\n".join(report_lines)
    with open(REPORT_FILE, "w") as f:
        f.write(report + "\n")
    print("\n" + report)

    print(f"\n[done] Completed in {time.time() - t0:.1f}s")
    print(f"[output] {REPORT_FILE}")
    print("[output] trailing_diagnostic.csv")
    print("[output] trade_excursion_analysis.csv")
    print("[output] cost_sensitivity.csv")
    print("[output] slippage_sensitivity.csv")


if __name__ == "__main__":
    main()
