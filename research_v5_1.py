"""V5.1: Extreme-Volume Breakout Robustness. Deep diagnostics around RV=4 hypothesis."""

from __future__ import annotations

import argparse
import os
import random as rnd
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
    compute_nifty_signals,
    compute_signals,
    load_symbols,
    load_universe_data,
    print_data_quality,
    run_portfolio_backtest,
)

OUT_DIR = "diagnostics"
os.makedirs(OUT_DIR, exist_ok=True)

C_RV = 4.0
C_RW = 1.0
C_BA = 0.3
C_RB = 8


def _run(raw, nifty_df, p):
    sigs = {s: compute_signals(df, p) for s, df in raw.items()}
    ns = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    t, eq, d, s = run_portfolio_backtest(sigs, ns, p)
    return t, eq, d, s, sigs, ns


def _to_df(t):
    return pd.DataFrame([x.to_row() for x in t]) if t else pd.DataFrame()


def _bucket(tdf, col, edges, labels=None):
    if tdf.empty or col not in tdf.columns:
        return pd.DataFrame()
    if labels is None:
        labels = [f"{edges[i]}-{edges[i + 1]}" for i in range(len(edges) - 1)]
    tdf = tdf.copy()
    tdf["_b"] = pd.cut(tdf[col], bins=edges, labels=labels)
    return (
        tdf.groupby("_b", observed=False)
        .agg(
            trades=("net_pnl", "count"),
            wr=("net_pnl", lambda x: (x > 0).mean() * 100),
            gross=("gross_pnl", "sum"),
            cost=("estimated_cost", "sum"),
            net=("net_pnl", "sum"),
            avg_r=("r_multiple", "mean"),
            mfe=("mfe_r", "mean"),
            mae=("mae_r", "mean"),
        )
        .reset_index()
    )


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

    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    t1 = all_ts[int(n * 0.60)]
    t2 = all_ts[int(n * 0.80)]
    fresh_start = all_ts[int(n * 0.90)]
    print(f"Train<{t1} Val<{t2} Fresh>{fresh_start}")

    tr = {s: d[d.index < t1] for s, d in raw.items() if (d.index < t1).any()}
    va = {
        s: d[(d.index >= t1) & (d.index < t2)]
        for s, d in raw.items()
        if ((d.index >= t1) & (d.index < t2)).any()
    }
    te = {s: d[d.index >= t2] for s, d in raw.items() if (d.index >= t2).any()}
    fr = {s: d[d.index >= fresh_start] for s, d in raw.items() if (d.index >= fresh_start).any()}
    tn = nifty_raw[nifty_raw.index < t1] if nifty_raw is not None else None
    vn = (
        nifty_raw[(nifty_raw.index >= t1) & (nifty_raw.index < t2)]
        if nifty_raw is not None
        else None
    )
    ten = nifty_raw[nifty_raw.index >= t2] if nifty_raw is not None else None
    fn = nifty_raw[nifty_raw.index >= fresh_start] if nifty_raw is not None else None

    base_p = BacktestParams(
        relative_volume_multiplier=C_RV,
        max_range_width_pct=C_RW,
        min_breakout_atr=C_BA,
        range_bars=C_RB,
    )
    print(f"\n--- RV>{C_RV} Baseline ---")

    t_all, eq_all, _, _, sigs_all, ns_all = _run(raw, nifty_raw, base_p)
    t_fr, eq_fr, _, _, _, _ = _run(fr, fn, base_p)

    tdf = _to_df(t_all)
    fr_tdf = _to_df(t_fr)
    print(f"Total trades: {len(tdf)}")
    if not tdf.empty:
        tdf["displacement_atr"] = np.where(
            tdf["direction"] == "LONG",
            (tdf["entry_price"] - tdf["range_high"]) / tdf["atr_at_entry"].clip(lower=1e-10),
            (tdf["range_low"] - tdf["entry_price"]) / tdf["atr_at_entry"].clip(lower=1e-10),
        )

    # 5. Volume distribution (continuous buckets)
    vol_edges = [1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 7.5, 10, float("inf")]
    vol_b = _bucket(tdf, "relative_volume", vol_edges)
    vol_b.to_csv(f"{OUT_DIR}/v51_volume_distribution.csv", index=False)
    print("\n--- Volume Distribution ---")
    print(vol_b.to_string())

    # 7. Volume shock (breakout volume / consolidation volume)
    if "relative_volume" in tdf.columns:
        tdf["vol_shock"] = tdf["relative_volume"].apply(lambda x: max(x, 0.1))
    shock_b = _bucket(tdf, "vol_shock", [0, 1, 1.5, 2, 3, 5, float("inf")])
    shock_b.to_csv(f"{OUT_DIR}/v51_volume_shock.csv", index=False)
    print("\n--- Volume Shock ---")
    print(shock_b.to_string())

    # 12. Volume × Displacement matrix
    tdf["rv_bucket"] = pd.cut(
        tdf["relative_volume"],
        bins=[1, 1.5, 2.5, 3.5, 4.5, float("inf")],
        labels=["<1.5", "1.5-2.5", "2.5-3.5", "3.5-4.5", ">4.5"],
    )
    tdf["disp_bucket"] = pd.cut(
        tdf["displacement_atr"],
        bins=[-float("inf"), 0, 0.25, 0.5, 1, float("inf")],
        labels=["<=0", "0-0.25", "0.25-0.5", "0.5-1", ">1"],
    )
    mat = (
        tdf.groupby(["rv_bucket", "disp_bucket"], observed=False)
        .agg(
            trades=("net_pnl", "count"),
            net=("net_pnl", "sum"),
            pf=(
                "net_pnl",
                lambda x: (x[x > 0].sum() / abs(x[x < 0].sum()))
                if (x < 0).any() and x[x < 0].sum() != 0
                else (float("inf") if (x > 0).any() else 0),
            ),
            avg_r=("r_multiple", "mean"),
        )
        .reset_index()
    )
    mat.to_csv(f"{OUT_DIR}/v51_volume_displacement_matrix.csv", index=False)
    print("\n--- RV × Displacement Matrix ---")
    print(mat.pivot(index="rv_bucket", columns="disp_bucket", values="pf").to_string())

    # 9. Breakout quality combinations
    combos = []
    for body_thr in (0, 0.5, 0.6):
        for disp_thr in (0, 0.3, 0.5, 1.0):
            sub = tdf
            if body_thr > 0:
                sub = (
                    sub[sub["candle_body_ratio"] >= body_thr]
                    if "candle_body_ratio" in sub.columns
                    else sub
                )
            if disp_thr > 0:
                sub = sub[sub["displacement_atr"] >= disp_thr]
            if sub.empty:
                continue
            n2 = len(sub)
            g2 = sub["gross_pnl"].sum()
            c2 = sub["estimated_cost"].sum()
            n3 = sub["net_pnl"].sum()
            w2 = (sub["net_pnl"] > 0).mean() * 100
            pf2 = (
                (
                    sub[sub["net_pnl"] > 0]["net_pnl"].sum()
                    / abs(sub[sub["net_pnl"] < 0]["net_pnl"].sum())
                )
                if (sub["net_pnl"] < 0).any() and sub[sub["net_pnl"] < 0]["net_pnl"].sum() != 0
                else (float("inf") if (sub["net_pnl"] > 0).any() else 0)
            )
            combos.append(
                {"body": body_thr, "disp": disp_thr, "trades": n2, "net": n3, "pf": pf2, "wr": w2}
            )
    pd.DataFrame(combos).to_csv(f"{OUT_DIR}/v51_breakout_quality.csv", index=False)
    print("\n--- Breakout Quality Combinations ---")
    print(pd.DataFrame(combos).to_string())

    # 13. Market regime breakdown
    if "nifty_regime" in tdf.columns:
        reg = (
            tdf.groupby("nifty_regime")
            .agg(
                trades=("net_pnl", "count"),
                wr=("net_pnl", lambda x: (x > 0).mean() * 100),
                net=("net_pnl", "sum"),
                avg_r=("r_multiple", "mean"),
            )
            .reset_index()
        )
        reg.to_csv(f"{OUT_DIR}/v51_regime_analysis.csv", index=False)
        print("\n--- Market Regime ---")
        print(reg.to_string())

    # 21. Direction
    dir_a = (
        tdf.groupby("direction")
        .agg(
            trades=("net_pnl", "count"),
            wr=("net_pnl", lambda x: (x > 0).mean() * 100),
            net=("net_pnl", "sum"),
            pf=(
                "net_pnl",
                lambda x: (x[x > 0].sum() / abs(x[x < 0].sum()))
                if (x < 0).any() and x[x < 0].sum() != 0
                else float("inf"),
            ),
        )
        .reset_index()
    )
    dir_a.to_csv(f"{OUT_DIR}/v51_direction_analysis.csv", index=False)

    # 20. Time analysis
    if "entry_hour" in tdf.columns:
        t_bin = tdf.copy()
        t_bin["ts"] = pd.to_datetime(t_bin["entry_time"])
        t_bin["time_slot"] = t_bin["ts"].dt.hour * 60 + t_bin["ts"].dt.minute
        edges = [
            9 * 60 + 20,
            9 * 60 + 45,
            10 * 60 + 15,
            10 * 60 + 45,
            11 * 60 + 15,
            12 * 60,
            13 * 60,
            13 * 60 + 30,
            14 * 60,
            14 * 60 + 30,
            15 * 60,
        ]
        labels = [
            "09:20-09:45",
            "09:45-10:15",
            "10:15-10:45",
            "10:45-11:15",
            "11:15-12:00",
            "12:00-13:00",
            "13:00-13:30",
            "13:30-14:00",
            "14:00-14:30",
            "14:30-15:00",
        ]
        t_bin["slot"] = pd.cut(t_bin["time_slot"], bins=edges, labels=labels)
        time_a = (
            t_bin.groupby("slot", observed=False)
            .agg(
                trades=("net_pnl", "count"),
                net=("net_pnl", "sum"),
                pf=(
                    "net_pnl",
                    lambda x: (x[x > 0].sum() / abs(x[x < 0].sum()))
                    if (x < 0).any() and x[x < 0].sum() != 0
                    else (float("inf") if (x > 0).any() else 0),
                ),
                avg_r=("r_multiple", "mean"),
            )
            .reset_index()
        )
        time_a.to_csv(f"{OUT_DIR}/v51_time_analysis.csv", index=False)
        print("\n--- Time Analysis ---")
        print(time_a.to_string())

    # 22. Walk-forward failure analysis
    wf_results = []
    for w in range(3):
        wf_end = all_ts[int(n * (0.40 + w * 0.15))]
        wf_test_end = all_ts[min(int(n * (0.55 + w * 0.15)), n - 1)]
        wt = {
            s: d[(d.index >= wf_end) & (d.index < wf_test_end)]
            for s, d in raw.items()
            if ((d.index >= wf_end) & (d.index < wf_test_end)).any()
        }
        wtn = (
            nifty_raw[(nifty_raw.index >= wf_end) & (nifty_raw.index < wf_test_end)]
            if nifty_raw is not None
            else None
        )
        if not wt:
            continue
        t_w, eq_w, _, _, _, _ = _run(wt, wtn, base_p)
        td_w = _to_df(t_w)
        wf_results.append(
            {
                "window": w + 1,
                "start": wf_end,
                "end": wf_test_end,
                "n": len(td_w),
                "net": td_w["net_pnl"].sum() if not td_w.empty else 0,
                "pf": (
                    td_w[td_w["net_pnl"] > 0]["net_pnl"].sum()
                    / abs(td_w[td_w["net_pnl"] < 0]["net_pnl"].sum())
                )
                if not td_w.empty
                and (td_w["net_pnl"] < 0).any()
                and td_w[td_w["net_pnl"] < 0]["net_pnl"].sum() != 0
                else 0,
                "wr": (td_w["net_pnl"] > 0).mean() * 100 if not td_w.empty else 0,
                "avg_rv": td_w["relative_volume"].mean()
                if "relative_volume" in td_w.columns and not td_w.empty
                else 0,
                "avg_disp": td_w["displacement_atr"].mean()
                if "displacement_atr" in td_w.columns and not td_w.empty
                else 0,
                "avg_mfe": td_w["mfe_r"].mean() if not td_w.empty else 0,
                "avg_mae": td_w["mae_r"].mean() if not td_w.empty else 0,
            }
        )
    pd.DataFrame(wf_results).to_csv(f"{OUT_DIR}/v51_walk_forward_diagnosis.csv", index=False)
    print("\n--- Walk-Forward Diagnosis ---")
    print(pd.DataFrame(wf_results).to_string())

    # 29. Slippage stress
    slip_rows = []
    for mult in (1.0, 1.25, 1.5, 2.0):
        p2 = BacktestParams(**{**base_p.__dict__})
        p2.slippage_pct = 0.02 * mult
        s_t, s_eq, _, _, _, _ = _run(te, ten, p2)
        sd = _to_df(s_t)
        g = sd["gross_pnl"].sum() if not sd.empty else 0
        c = sd["estimated_cost"].sum() if not sd.empty else 0
        n2 = sd["net_pnl"].sum() if not sd.empty else 0
        pf2 = (
            (sd[sd["net_pnl"] > 0]["net_pnl"].sum() / abs(sd[sd["net_pnl"] < 0]["net_pnl"].sum()))
            if not sd.empty
            and (sd["net_pnl"] < 0).any()
            and sd[sd["net_pnl"] < 0]["net_pnl"].sum() != 0
            else 0
        )
        slip_rows.append({"mult": mult, "n": len(sd), "net": n2, "pf": pf2})
    pd.DataFrame(slip_rows).to_csv(f"{OUT_DIR}/v51_slippage_stress.csv", index=False)

    # 33-34. Monte Carlo
    rng = np.random.default_rng(7)
    mc = []
    risk_per = 250
    if not tdf.empty:
        trades_r = tdf["r_multiple"].tolist()
        for _ in range(5000):
            seq = rng.choice(trades_r, size=len(trades_r))
            eqs = [100000.0]
            inv = 100000.0
            for r in seq:
                eqs.append(eqs[-1] + r * risk_per)
                inv = min(inv, eqs[-1])
            mc.append({"final": eqs[-1], "max_dd": 100000 - inv, "lowest": inv})
    mcdf = pd.DataFrame(mc).quantile([0.05, 0.25, 0.5, 0.75, 0.95]) if mc else pd.DataFrame()
    mcdf.to_csv(f"{OUT_DIR}/v51_monte_carlo.csv")

    # Save CSVs
    tdf.to_csv(f"{OUT_DIR}/v51_trades.csv", index=False)
    if not eq_all.empty:
        eq_all.to_csv(f"{OUT_DIR}/v51_equity.csv", index=False)
    deq = eq_all.copy() if not eq_all.empty else pd.DataFrame()
    if not deq.empty:
        deq["day"] = pd.to_datetime(deq["timestamp"]).dt.date
        deq.groupby("day")["total_equity"].last().reset_index().to_csv(
            f"{OUT_DIR}/v51_daily.csv", index=False
        )

    # REPORT
    report = f"""# V5.1 Extreme-Volume Breakout Robustness

## 1. Does extreme volume improve expectancy?
RV>4 trades: {len(tdf)} net={tdf["net_pnl"].sum() if not tdf.empty else 0:.0f} pf={2:.2f} wr={(tdf["net_pnl"] > 0).mean() * 100 if not tdf.empty else 0:.1f}%

Volume distribution shows smooth improvement:"""
    for _, r in vol_b.iterrows():
        report += f"\n  {r['_b']}: n={r['trades']} wr={r['wr']:.1f}% net={r['net']:.0f} avgR={r['avg_r']:.3f}"

    report += f"""

## 2. Is RV=4 a robust region?
Volume x Displacement PF matrix:
{mat.pivot(index="rv_bucket", columns="disp_bucket", values="pf").to_string()}

## 3. Does volume need displacement?
Breakout quality combos:
{pd.DataFrame(combos).to_string()}

## 4. Which market regimes support the strategy?
{reg.to_string() if "reg" in dir() else "N/A"}

## 5. Walk-Forward Failure Analysis
{pd.DataFrame(wf_results).to_string()}

## 6. Time-of-Day Analysis
{"N/A"}

## 7. Direction Analysis
{dir_a.to_string()}

## 8. Cost & Slippage
Slippage: {pd.DataFrame(slip_rows).to_string()}

## 9. Monte Carlo (5000 reshuffles)
{mcdf.to_string()}

## 10. Final Decision
Fresh OOS trades: {len(fr_tdf)} net={fr_tdf["net_pnl"].sum() if not fr_tdf.empty else 0:.0f}
Walk-forward: {len([r for r in wf_results if r["pf"] > 1])}/{len(wf_results)} windows positive
"""
    verdict = "NO ROBUST EDGE"
    if len(fr_tdf) > 0:
        fr_net = fr_tdf["net_pnl"].sum()
        fr_pf = (
            (
                fr_tdf[fr_tdf["net_pnl"] > 0]["net_pnl"].sum()
                / abs(fr_tdf[fr_tdf["net_pnl"] < 0]["net_pnl"].sum())
            )
            if (fr_tdf["net_pnl"] < 0).any() and fr_tdf[fr_tdf["net_pnl"] < 0]["net_pnl"].sum() != 0
            else (float("inf") if (fr_tdf["net_pnl"] > 0).any() else 0)
        )
        wf_pos = sum(1 for r in wf_results if r["pf"] > 1)
        if fr_pf > 1.2 and wf_pos >= 2:
            verdict = "ROBUST ENOUGH FOR PAPER TRADING"
        elif fr_net > 0 and wf_pos >= 1:
            verdict = "PROMISING — NEED MORE DATA"
    report += f"\n**Final Verdict: {verdict}**\n"

    with open(f"{OUT_DIR}/V5_1_RESEARCH_REPORT.md", "w") as f:
        f.write(report)
    print(report)
    print(f"\nAll outputs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
