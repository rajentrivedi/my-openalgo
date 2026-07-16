"""
V4.1 Robustness Validation
===========================
Deep robustness testing of the V4 candidate region.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import Counter
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

# ---------------------------------------------------------------------------
# BASE RUN
# ---------------------------------------------------------------------------
OUT_DIR = "diagnostics"
os.makedirs(OUT_DIR, exist_ok=True)

TRAIN_FRAC = 0.60
VAL_FRAC = 0.20


def _run(raw, nifty_df, p):
    sigs = {s: compute_signals(df, p) for s, df in raw.items()}
    ns = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    t, eq, d, s = run_portfolio_backtest(sigs, ns, p)
    return t, eq, d, s


def _stats(t, eq):
    if not t:
        return {
            "n": 0,
            "gross": 0,
            "cost": 0,
            "net": 0,
            "wr": 0,
            "pf": 0,
            "avg_r": 0,
            "sharpe": 0,
            "max_dd": 0,
        }
    d = pd.DataFrame([x.to_row() for x in t])
    g = float(d["gross_pnl"].sum())
    c = float(d["estimated_cost"].sum())
    n = float(d["net_pnl"].sum())
    w = d[d["net_pnl"] > 0]
    lc = d[d["net_pnl"] < 0]
    pf_ = (
        abs(w["net_pnl"].sum() / lc["net_pnl"].sum())
        if not lc.empty and lc["net_pnl"].sum() != 0
        else float("inf")
        if len(w)
        else 0
    )
    wr_ = (d["net_pnl"] > 0).mean() * 100
    eqs = eq["total_equity"] if not eq.empty else pd.Series([100000])
    p_ = eqs.cummax()
    dd = ((p_ - eqs) / p_).max() if p_.max() > 0 else 0
    avg_r = float(d["r_multiple"].mean())
    # sharpe (daily)
    if not eq.empty and len(eqs) > 5:
        ex = eq.copy()
        ex["day"] = pd.to_datetime(ex["timestamp"]).dt.date
        de = ex.groupby("day")["total_equity"].last()
        ret = de.pct_change().dropna()
        sh = float(ret.mean() / ret.std() * np.sqrt(252)) if len(ret) > 1 and ret.std() > 0 else 0
    else:
        sh = 0
    return {
        "n": len(d),
        "gross": g,
        "cost": c,
        "net": n,
        "wr": wr_,
        "pf": pf_,
        "avg_r": avg_r,
        "sharpe": sh,
        "max_dd": float(dd),
    }


# ---------------------------------------------------------------------------
# CANDIDATE REGION
# ---------------------------------------------------------------------------
# The candidate that showed promise in V4:
C_RV = 1.2
C_RW = 1.0
C_BA = 0.30
C_RB = 8
C_SES = "BOTH"
C_DIR = "both"


def _mkp(p=None, rv=None, rw=None, ba=None, ses=None, dirs=None, rb=None, cost=None, slip=None):
    base = p or BacktestParams()
    kw = dict(base.__dict__)
    if rv is not None:
        kw["relative_volume_multiplier"] = rv
    if rw is not None:
        kw["max_range_width_pct"] = rw
    if ba is not None:
        kw["min_breakout_atr"] = ba
    if ses is not None:
        kw["session_switch"] = ses
    if rb is not None:
        kw["range_bars"] = rb
    if cost is not None:
        kw["estimated_cost_pct"] = cost
    if slip is not None:
        kw["slippage_pct"] = slip
    pn = BacktestParams(**kw)
    if dirs == "long":
        pn.long_only = True
        pn.short_only = False
    elif dirs == "short":
        pn.long_only = False
        pn.short_only = True
    else:
        pn.long_only = False
        pn.short_only = False
    return pn


def _split(raw, nifty, traind, vald, testd=None, freshd=None):
    tr = {s: d[d.index < traind] for s, d in raw.items() if (d.index < traind).any()}
    va = {
        s: d[(d.index >= traind) & (d.index < vald)]
        for s, d in raw.items()
        if ((d.index >= traind) & (d.index < vald)).any()
    }
    te = (
        {
            s: d[(d.index >= vald) & (d.index < testd)]
            for s, d in raw.items()
            if ((d.index >= vald) & (d.index < testd)).any()
        }
        if testd
        else {}
    )
    fr = (
        {s: d[(d.index >= testd)] for s, d in raw.items() if (d.index >= testd).any()}
        if testd and freshd
        else {}
    )
    return tr, va, te, fr


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=20, help="number of symbols")
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
    t1 = all_ts[int(n * TRAIN_FRAC)]
    t2 = all_ts[int(n * (TRAIN_FRAC + VAL_FRAC))]
    locked = all_ts[-1] if len(all_ts) > 1 else t2  # full date as end
    # fresh OOS = any data after locked test start (t2). Use last 10% of timeline as fresh.
    fresh_start = all_ts[int(n * 0.90)]
    locked_end = all_ts[-1]
    print(f"Train<{t1} Val<{t2} Locked<{locked_end} Fresh>{fresh_start}")

    tr, va, te, _fr = _split(raw, nifty, t1, t2, locked_end, locked_end)
    tr_n, va_n, te_n = (
        nifty[nifty.index < t1] if nifty is not None else None,
        nifty[(nifty.index >= t1) & (nifty.index < t2)] if nifty is not None else None,
        nifty[(nifty.index >= t2)] if nifty is not None else None,
    )

    # ---- Fresh OOS ----
    fr = {s: d[d.index >= fresh_start] for s, d in raw.items() if (d.index >= fresh_start).any()}
    fr_n = nifty[nifty.index >= fresh_start] if nifty is not None else None

    base_p = _mkp(rv=C_RV, rw=C_RW, ba=C_BA, rb=C_RB)

    t_fr, eq_fr, _, _ = _run(fr, fr_n, base_p)
    frs = _stats(t_fr, eq_fr)
    print(
        f"\nFresh OOS: trades={frs['n']} net={frs['net']:.1f} pf={frs['pf']:.3f} wr={frs['wr']:.1f}%"
    )

    # ---- Parameter robustness grid around candidate ----
    rv_grid = [1.0, 1.2, 1.5]
    rw_grid = [0.90, 1.00, 1.10]
    ba_grid = [0.20, 0.30, 0.40]
    ses_grid = ["BOTH"]
    dir_grid = ["both"]
    rb_grid = [8]

    param_rows = []
    for rv in rv_grid:
        for rw in rw_grid:
            for ba in ba_grid:
                for ses in ses_grid:
                    for dirs in dir_grid:
                        for rb in rb_grid:
                            p = _mkp(rv=rv, rw=rw, ba=ba, ses=ses, dirs=dirs, rb=rb)
                            t_tr, eq_tr, _, _ = _run(tr, tr_n, p)
                            t_va, eq_va, _, _ = _run(va, va_n, p)
                            t_te, eq_te, _, _ = _run(te, te_n, p)
                            s_tr = _stats(t_tr, eq_tr)
                            s_va = _stats(t_va, eq_va)
                            s_te = _stats(t_te, eq_te)
                            param_rows.append(
                                {
                                    "rv": rv,
                                    "rw": rw,
                                    "ba": ba,
                                    "session": ses,
                                    "direction": dirs,
                                    "rb": rb,
                                    "tr_n": s_tr["n"],
                                    "tr_net": round(s_tr["net"]),
                                    "tr_pf": round(s_tr["pf"], 3),
                                    "va_net": round(s_va["net"]),
                                    "va_pf": round(s_va["pf"], 3),
                                    "te_net": round(s_te["net"]),
                                    "te_pf": round(s_te["pf"], 3),
                                }
                            )
    pdf = pd.DataFrame(param_rows)
    pdf.to_csv(f"{OUT_DIR}/v41_candidate_comparison.csv", index=False)

    # ---- Parameter stability score ----
    stab_rows = []
    for grp, col, vals in [("rv", "rv", rv_grid), ("rw", "rw", rw_grid), ("ba", "ba", ba_grid)]:
        for v in vals:
            sub = pdf[pdf[col] == v]
            if sub.empty:
                continue
            oos_pf = sub["va_pf"]
            stab_rows.append(
                {
                    "param": f"{grp}={v}",
                    "mean_pf": oos_pf.mean(),
                    "std_pf": oos_pf.std(),
                    "pct_pos": (oos_pf > 0).mean() * 100,
                    "pct_gt1": (oos_pf > 1).mean() * 100,
                    "pct_gt1.2": (oos_pf > 1.2).mean() * 100,
                    "best_pf": oos_pf.max(),
                    "worst_pf": oos_pf.min(),
                    "class": "FRAGILE"
                    if oos_pf.max() < 1.2
                    else "MODERATELY ROBUST"
                    if oos_pf.mean() < 1.05
                    else "ROBUST",
                }
            )
    sdf = pd.DataFrame(stab_rows)
    sdf.to_csv(f"{OUT_DIR}/v41_parameter_robustness.csv", index=False)

    # ---- Walk-forward ----
    wf_rows = []
    for w in range(3):
        wf_end = all_ts[int(n * (0.50 + w * 0.15))]
        wf_test = all_ts[int(n * (0.60 + w * 0.15))]
        wr = {s: d[d.index < wf_end] for s, d in raw.items() if (d.index < wf_end).any()}
        wt = {
            s: d[(d.index >= wf_end) & (d.index < wf_test)]
            for s, d in raw.items()
            if ((d.index >= wf_end) & (d.index < wf_test)).any()
        }
        wr_n = nifty[nifty.index < wf_end] if nifty is not None else None
        wt_n = (
            nifty[(nifty.index >= wf_end) & (nifty.index < wf_test)] if nifty is not None else None
        )
        tw, eqw, _, _ = _run(wr, wr_n, base_p)
        ttw, eqt, _, _ = _run(wt, wt_n, base_p)
        sw = _stats(tw, eqw)
        st = _stats(ttw, eqt)
        wf_rows.append(
            {
                "window": w + 1,
                "train_start": all_ts[0],
                "train_end": wf_end,
                "test_start": wf_end,
                "test_end": wf_test,
                "train_n": sw["n"],
                "train_net": round(sw["net"]),
                "test_n": st["n"],
                "test_net": round(st["net"]),
                "test_pf": round(st["pf"], 3),
                "test_sharpe": st["sharpe"],
                "test_max_dd": round(st["max_dd"], 2),
                "test_avg_r": round(st["avg_r"], 3),
                "test_wr": round(st["wr"], 1),
            }
        )
    wdf = pd.DataFrame(wf_rows)
    wdf.to_csv(f"{OUT_DIR}/v41_walk_forward.csv", index=False)

    # ---- Monthly analysis ----
    full_tr, eq_full, _, _ = _run(raw, nifty, base_p)
    if full_tr:
        mdf = pd.DataFrame([t.to_row() for t in full_tr])
        mdf["month"] = pd.to_datetime(mdf["date"]).dt.to_period("M")
        monthly = (
            mdf.groupby("month")
            .agg(
                trades=("net_pnl", "count"),
                wr=("net_pnl", lambda x: (x > 0).mean() * 100),
                gross=("gross_pnl", "sum"),
                cost=("estimated_cost", "sum"),
                net=("net_pnl", "sum"),
                pf_=(
                    "net_pnl",
                    lambda x: (x[x > 0].sum() / abs(x[x < 0].sum()))
                    if (x < 0).any() and x[x < 0].sum() != 0
                    else float("inf"),
                ),
                avg_r=("r_multiple", "mean"),
                max_dd=("net_pnl", "min"),
            )
            .reset_index()
        )
        monthly["month"] = monthly["month"].astype(str)
        monthly.to_csv(f"{OUT_DIR}/v41_monthly_analysis.csv", index=False)
    else:
        monthly = pd.DataFrame({"month": [], "net": []})
        monthly.to_csv(f"{OUT_DIR}/v41_monthly_analysis.csv", index=False)

    # ---- Symbol analysis ----
    if full_tr:
        sym_df = pd.DataFrame([t.to_row() for t in full_tr])
        saw = (
            sym_df.groupby("symbol")
            .agg(
                trades=("net_pnl", "count"),
                wr=("net_pnl", lambda x: (x > 0).mean() * 100),
                gross=("gross_pnl", "sum"),
                cost=("estimated_cost", "sum"),
                net=("net_pnl", "sum"),
                pf_=(
                    "net_pnl",
                    lambda x: (x[x > 0].sum() / abs(x[x < 0].sum()))
                    if (x < 0).any() and x[x < 0].sum() != 0
                    else float("inf"),
                ),
                avg_r=("r_multiple", "mean"),
                max_dd=("net_pnl", "min"),
            )
            .reset_index()
            .sort_values("net", ascending=False)
        )
        saw.to_csv(f"{OUT_DIR}/v41_symbol_analysis.csv", index=False)
        best_sym = saw.iloc[0] if not saw.empty else None
        top3 = saw.head(3)["symbol"].tolist() if len(saw) >= 3 else saw["symbol"].tolist()
    else:
        saw = pd.DataFrame()

    # ---- Symbol stress (remove best, remove top 3) ----
    sym_stress = {}
    if full_tr:
        for label, rm_list in [
            ("full", []),
            ("remove_best", [saw.iloc[0]["symbol"]] if not saw.empty else []),
            ("remove_top3", top3),
        ]:
            rmd = {s: d for s, d in raw.items() if s not in rm_list}
            t_s, eq_s, _, _ = _run(rmd, nifty, base_p)
            ss = _stats(t_s, eq_s)
            sym_stress[label] = ss
        pd.DataFrame([{"scenario": k, **v} for k, v in sym_stress.items()]).to_csv(
            f"{OUT_DIR}/v41_symbol_stress.csv", index=False
        )

    # ---- Random universe (100 iterations) ----
    random.seed(42)
    np.random.seed(42)
    ru_rows = []
    for it in range(100):
        sub_syms = random.sample(list(raw.keys()), max(3, int(len(raw) * 0.8)))
        rd = {s: raw[s] for s in sub_syms if s in raw}
        t_ru, eq_ru, _, _ = _run(rd, nifty, base_p)
        rs = _stats(t_ru, eq_ru)
        ru_rows.append(rs)
    rudf = pd.DataFrame(ru_rows)
    rudf.quantile([0.05, 0.25, 0.50, 0.75, 0.95]).to_csv(f"{OUT_DIR}/v41_random_universe.csv")
    ru_pct = (rudf["net"] > 0).mean() * 100 if len(rudf) else 0
    ru_pf1 = (rudf["pf"] > 1).mean() * 100 if len(rudf) else 0

    # ---- Trade-level Monte Carlo (5000) ----
    if full_tr:
        trades_r = [t.r_multiple for t in full_tr]
        np.random.seed(7)
        risk_per = 250
        mc_rows = []
        initial = 100000.0
        for _ in range(5000):
            seq = np.random.choice(trades_r, size=len(trades_r), replace=True)
            eq_curve = [initial]
            inv_min = initial
            for r in seq:
                pnl = r * risk_per
                eq_curve.append(eq_curve[-1] + pnl)
                inv_min = min(inv_min, eq_curve[-1])
            mc_rows.append({"final": eq_curve[-1], "max_dd": initial - inv_min, "lowest": inv_min})
        mcdf = pd.DataFrame(mc_rows).quantile([0.05, 0.25, 0.50, 0.75, 0.95])
        mcdf.to_csv(f"{OUT_DIR}/v41_trade_monte_carlo.csv")

    # ---- Monthly Monte Carlo (1000) ----
    if not monthly.empty and len(monthly) > 1:
        np.random.seed(13)
        mmc = []
        for _ in range(1000):
            resampled = np.random.choice(monthly["net"], size=len(monthly), replace=True)
            mmc.append({"final": sum(resampled), "min": min(resampled), "max": max(resampled)})
        mmcdf = pd.DataFrame(mmc).quantile([0.05, 0.25, 0.50, 0.75, 0.95])
        mmcdf.to_csv(f"{OUT_DIR}/v41_monthly_monte_carlo.csv")

    # ---- Cost stress ----
    cost_rows = []
    for cp in (0.0, 0.01, 0.02, 0.03, 0.05):
        p = _mkp(rv=C_RV, rw=C_RW, ba=C_BA, rb=C_RB, ses=C_SES, dirs=C_DIR, cost=cp)
        t_c, eq_c, _, _ = _run(te, te_n, p) if te else _run(va, va_n, p)
        sc = _stats(t_c, eq_c)
        cost_rows.append({"cost_pct": cp, **sc})
    pd.DataFrame(cost_rows).to_csv(f"{OUT_DIR}/v41_cost_stress.csv", index=False)

    # ---- Slippage stress ----
    slip_rows = []
    for sp in (0.0, 0.01, 0.02, 0.03):
        p = _mkp(rv=C_RV, rw=C_RW, ba=C_BA, rb=C_RB, ses=C_SES, dirs=C_DIR, slip=sp)
        t_s, eq_s, _, _ = _run(te, te_n, p) if te else _run(va, va_n, p)
        ss = _stats(t_s, eq_s)
        slip_rows.append({"slippage_pct": sp, **ss})
    pd.DataFrame(slip_rows).to_csv(f"{OUT_DIR}/v41_slippage_stress.csv", index=False)

    # ---- Execution stress (slippage multiplier) ----
    exec_rows = []
    for mul in (1.0, 1.25, 1.50, 2.0):
        sp = 0.02 * mul
        p = _mkp(rv=C_RV, rw=C_RW, ba=C_BA, rb=C_RB, ses=C_SES, dirs=C_DIR, slip=sp)
        t_e, eq_e, _, _ = _run(te, te_n, p) if te else _run(va, va_n, p)
        se = _stats(t_e, eq_e)
        exec_rows.append({"slippage_mult": mul, **se})
    pd.DataFrame(exec_rows).to_csv(f"{OUT_DIR}/v41_execution_stress.csv", index=False)

    # ---- Trade analysis (buckets) ----
    if full_tr:
        tadf = pd.DataFrame([t.to_row() for t in full_tr])
        tadf.to_csv(f"{OUT_DIR}/v41_trade_analysis.csv", index=False)
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v41_trade_analysis.csv")

    # ---- Equity/daily ----
    if not eq_full.empty:
        eq_full.to_csv(f"{OUT_DIR}/v41_equity.csv", index=False)
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v41_equity.csv")

    if full_tr and not eq_full.empty:
        # daily from equity
        deq = eq_full.copy()
        deq["day"] = pd.to_datetime(deq["timestamp"]).dt.date
        daily = (
            deq.groupby("day")
            .agg(eq=("total_equity", "last"), dd=("drawdown", "max"))
            .reset_index()
        )
        daily.to_csv(f"{OUT_DIR}/v41_daily.csv", index=False)
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v41_daily.csv")

    # ---- Validation report ----
    v_data = {
        "Fresh OOS": frs["net"],
        "Walk-forward": [r["test_net"] for r in wf_rows],
        "Param robustness": pdf,
        "Symbol count": saw["symbol"].nunique() if not saw.empty else 0,
        "Random universe profitable%": ru_pct,
        "Monte Carlo 95% DD": mcdf.loc[0.95, "max_dd"] if not mcdf.empty else 0,
    }
    pd.DataFrame([v_data]).to_csv(f"{OUT_DIR}/v41_validation.csv", index=False)

    # ---- REPORT ----
    lines = []
    lines.append("# V4.1 Robustness Validation Report")
    lines.append("")

    # 2 V4 baseline
    lines.append("## 2. V4 Baseline Candidate")
    lines.append(f"- RV threshold: {C_RV}")
    lines.append(f"- Range width: {C_RW}%")
    lines.append(f"- Breakout strength: {C_BA} ATR")
    lines.append(f"- Range bars: {C_RB}")
    lines.append(f"- Session: {C_SES}")
    lines.append(f"- Direction: {C_DIR}")
    lines.append("")

    # 3 Fresh OOS
    lines.append("## 3. Fresh Out-of-Sample")
    lines.append(f"- Period: {fresh_start} → {locked_end}")
    lines.append(f"- Trades: {frs['n']}")
    lines.append(f"- Net P&L: {frs['net']:.2f}")
    lines.append(f"- PF: {frs['pf']:.3f}")
    lines.append(f"- Win rate: {frs['wr']:.1f}%")
    lines.append("")

    # 4 Walk-forward
    lines.append("## 4. Walk-Forward Validation")
    for r in wf_rows:
        lines.append(
            f"Window {r['window']}: test_net={r['test_net']:.0f} pf={r['test_pf']:.3f} sharpe={r['test_sharpe']:.2f}"
        )
    lines.append("")

    # 5 Parameter robustness
    lines.append("## 5. Parameter Robustness")
    lines.append(sdf.to_string())
    lines.append("")

    # 6 Symbol robustness
    lines.append("## 6. Symbol Robustness")
    if not saw.empty:
        lines.append(f"Profitable symbols: {(saw['net'] > 0).sum()} / {len(saw)}")
        lines.append(f"Top-3 contribution: {saw.head(3)['net'].sum():.0f} / {saw['net'].sum():.0f}")
        lines.append(
            f"Symbol concentration risk: {sym_stress.get('remove_best', {}).get('net', 0):.1f} (remove best)"
        )
        lines.append(
            f"Symbol stress (no top3): {sym_stress.get('remove_top3', {}).get('net', 0):.1f}"
        )
    lines.append("")

    # 7 Monthly
    lines.append("## 7. Monthly Robustness")
    if not monthly.empty:
        lines.append(monthly.to_string())
    lines.append("")

    # 8 Monte Carlo
    lines.append("## 8. Monte Carlo")
    lines.append("### Trade MC (5000 reshuffles)")
    lines.append(mcdf.to_string())
    lines.append("### Monthly MC (1000 resamples)")
    if not mmcdf.empty:
        lines.append(mmcdf.to_string())
    lines.append("")

    # Random universe
    lines.append("## 9. Random Universe (100 iterations)")
    lines.append(f"Pct profitable: {ru_pct:.1f}%")
    lines.append(f"Pct PF>1: {ru_pf1:.1f}%")
    lines.append(rudf.describe().to_string())
    lines.append("")

    # Cost/slippage
    lines.append("## 10. Cost & Slippage Stress")
    lines.append("### Cost:")
    lines.append(pd.DataFrame(cost_rows).to_string())
    lines.append("### Slippage:")
    lines.append(pd.DataFrame(slip_rows).to_string())
    lines.append("### Execution degradation:")
    lines.append(pd.DataFrame(exec_rows).to_string())
    lines.append("")

    # Long/short
    lines.append("## 11. Long vs Short")
    if full_tr:
        tdf = pd.DataFrame([t.to_row() for t in full_tr])
        for d in ("LONG", "SHORT"):
            sub = tdf[tdf["direction"] == d]
            lines.append(
                f"{d}: n={len(sub)} net={sub['net_pnl'].sum():.0f} wr={(sub['net_pnl'] > 0).mean() * 100:.1f}%"
            )
    lines.append("")

    # Session
    lines.append("## 12. Session")
    if full_tr:
        for s in ("S1", "S2"):
            sub = tdf[tdf["session"] == s]
            lines.append(
                f"{s}: n={len(sub)} net={sub['net_pnl'].sum():.0f} wr={(sub['net_pnl'] > 0).mean() * 100:.1f}%"
            )
    lines.append("")

    # Entry quality
    lines.append("## 13. Entry Quality Analysis")
    if full_tr:
        for col, label in [("relative_volume", "RelVol"), ("range_width_pct", "RangeW%")]:
            edges = {
                "relative_volume": [1.0, 1.2, 1.5, 2, 3, np.inf],
                "range_width_pct": [0, 0.5, 0.75, 1.0, 1.25, np.inf],
            }
            ed = edges.get(col, [1, 2, 3])
            for i in range(len(ed) - 1):
                sub = tdf[(tdf[col] >= ed[i]) & (tdf[col] < ed[i + 1])]
                if not sub.empty:
                    lines.append(
                        f"  {ed[i]:.2f}-{ed[i + 1]:.2f}: n={len(sub)} net={sub['net_pnl'].sum():.0f} wr={(sub['net_pnl'] > 0).mean() * 100:.1f}%"
                    )
    lines.append("")

    # Data-snooping
    lines.append("## 14. Data-Snooping Risk")
    lines.append(f"Configurations tested: {len(pdf)}")
    lines.append(f"Profitable on val: {(pdf['va_net'] > 0).sum()}")
    lines.append(f"PF>1 on val: {(pdf['va_pf'] > 1).sum()}")
    lines.append(f"Best PF on val: {pdf['va_pf'].max():.3f}")
    lines.append(f"Median PF on val: {pdf['va_pf'].median():.3f}")
    lines.append("")

    # Final
    lines.append("## 15. Final Assessment")
    verdict = "NO ROBUST EDGE FOUND"
    if frs["pf"] > 1.2 and frs["net"] > 0 and all(r["test_pf"] > 1 for r in wf_rows):
        verdict = "ROBUST ENOUGH FOR PAPER TRADING"
    elif frs["pf"] > 1 and frs["net"] > 0:
        verdict = "PROMISING BUT NEEDS MORE DATA"
    lines.append(f"**{verdict}**")
    lines.append("")

    # Decision table
    lines.append("## Decision Table")
    lines.append("| Test | Result | Status |")
    lines.append("| --- | --- | --- |")
    lines.append(
        f"| Fresh OOS | net={frs['net']:.0f} pf={frs['pf']:.2f} | {'PASS' if frs['pf'] > 1 else 'FAIL'} |"
    )
    lines.append(
        f"| Walk-forward avg | net={np.mean([r['test_net'] for r in wf_rows]):.0f} | {'PASS' if all(r['test_pf'] > 1 for r in wf_rows) else 'FAIL'} |"
    )
    lines.append(
        f"| Param robustness | mean_pf={sdf['mean_pf'].mean():.3f} | {'PASS' if sdf['mean_pf'].mean() > 1 else 'FAIL'} |"
    )
    lines.append(
        f"| Symbol stress | net(remove_best)={sym_stress.get('remove_best', {}).get('net', 0):.0f} | {'PASS' if sym_stress.get('remove_best', {}).get('pf', 0) > 1 else 'FAIL'} |"
    )
    lines.append(
        f"| Random universe pct profitable | {ru_pct:.1f}% | {'PASS' if ru_pct > 50 else 'FAIL'} |"
    )
    lines.append(
        f"| Cost stress @0.03% | cf={cost_rows[3]['net']:.0f} | {'PASS' if cost_rows[3]['pf'] > 1 else 'FAIL'} |"
    )
    lines.append(
        f"| Slippage stress @0.02% | sf={slip_rows[2]['net']:.0f} | {'PASS' if slip_rows[2]['pf'] > 1 else 'FAIL'} |"
    )
    lines.append(
        f"| Monte Carlo 95% DD | {mcdf.loc[0.95, 'max_dd']:.0f} of {initial} | {'PASS' if mcdf.loc[0.95, 'max_dd'] < 15000 else 'FAIL'} |"
        if not mcdf.empty
        else "| Monte Carlo | N/A | SKIP |"
    )
    lines.append("")

    report = "\n".join(lines)
    with open(f"{OUT_DIR}/V4_1_ROBUSTNESS_REPORT.md", "w") as f:
        f.write(report + "\n")
    print(report)
    print(f"\nV4.1 done — all outputs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
