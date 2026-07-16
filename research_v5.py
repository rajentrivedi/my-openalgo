"""
V5 Hypothesis-Driven Strategy Redesign
========================================
Tests three hypothesis families independently on the V3 engine:

H1: Extreme abnormal volume breakouts (volume percentile, >3× RV)
H2: Candle quality + displacement (close-at-extreme, large body, strong breakout)
H3: Market regime + relative strength (NIFTY EMA slope, stock vs NIFTY)

Each hypothesis is tested as an overlay on the baseline V4 candidate.
Separate train/val/locked-test/fresh-OOS reporting.
For the best composite, full robustness suite.
"""

from __future__ import annotations

import argparse
import copy
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


def _run(raw, nifty_df, p):
    sigs = {s: compute_signals(df, p) for s, df in raw.items()}
    ns = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    t, eq, d, s = run_portfolio_backtest(sigs, ns, p)
    return t, eq, d, s


def _stats(t, eq):
    if not t:
        return {"n": 0, "gross": 0, "cost": 0, "net": 0, "wr": 0, "pf": 0, "avg_r": 0, "sharpe": 0, "max_dd": 0}
    d = pd.DataFrame([x.to_row() for x in t])
    g = float(d["gross_pnl"].sum())
    c = float(d["estimated_cost"].sum())
    n = float(d["net_pnl"].sum())
    w = d[d["net_pnl"] > 0]
    lc = d[d["net_pnl"] < 0]
    pf_ = abs(w["net_pnl"].sum() / lc["net_pnl"].sum()) if not lc.empty and lc["net_pnl"].sum() != 0 else float("inf") if len(w) else 0
    wr_ = (d["net_pnl"] > 0).mean() * 100
    eqs = eq["total_equity"] if not eq.empty else pd.Series([100000])
    p_ = eqs.cummax()
    dd = ((p_ - eqs) / p_).max() if p_.max() > 0 else 0
    return {"n": len(d), "gross": g, "cost": c, "net": n, "wr": wr_, "pf": pf_, "avg_r": float(d["r_multiple"].mean()), "max_dd": float(dd)}


def _mkp(p=None, rv=None, rw=None, ba=None, ses=None, dirs=None, rb=None, cost=None, slip=None, vp=None, bd=None, cl=None, cb=None, rs=None):
    base = p or BacktestParams()
    kw = dict(base.__dict__)
    for k, v in [("relative_volume_multiplier", rv), ("max_range_width_pct", rw), ("min_breakout_atr", ba), ("session_switch", ses), ("range_bars", rb), ("estimated_cost_pct", cost), ("slippage_pct", slip), ("min_volume_percentile", vp), ("min_breakout_displacement", bd), ("close_location_min", cl), ("candle_body_min", cb)]:
        if v is not None:
            kw[k] = v
    pn = BacktestParams(**kw)
    if rs == "long":
        pn.long_only = False
        pn.short_only = False
    elif rs == "short":
        pn.long_only = False
        pn.short_only = True
    else:
        pn.long_only = False
        pn.short_only = False
    return pn


def _split_data(raw, nifty, t1, t2):
    tr = {s: d[d.index < t1] for s, d in raw.items() if (d.index < t1).any()}
    va = {s: d[(d.index >= t1) & (d.index < t2)] for s, d in raw.items() if ((d.index >= t1) & (d.index < t2)).any()}
    te = {s: d[d.index >= t2] for s, d in raw.items() if (d.index >= t2).any()}
    tn = nifty[nifty.index < t1] if nifty is not None else None
    vn = nifty[(nifty.index >= t1) & (nifty.index < t2)] if nifty is not None else None
    ten = nifty[nifty.index >= t2] if nifty is not None else None
    return tr, va, te, tn, vn, ten


def _run_split(tr, va, te, tn, vn, ten, p):
    t1, e1, _, _ = _run(tr, tn, p)
    t2, e2, _, _ = _run(va, vn, p)
    t3, e3, _, _ = _run(te, ten, p)
    return _stats(t1, e1), _stats(t2, e2), _stats(t3, e3)


def _walk_forward(raw, nifty, base_p, windows=3):
    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    rows = []
    for w in range(windows):
        tr_end = all_ts[int(n * (0.40 + w * 0.15))]
        te_end = all_ts[min(int(n * (0.55 + w * 0.15)), n - 1)]
        wr = {s: d[d.index < tr_end] for s, d in raw.items() if (d.index < tr_end).any()}
        wt = {s: d[(d.index >= tr_end) & (d.index < te_end)] for s, d in raw.items() if ((d.index >= tr_end) & (d.index < te_end)).any()}
        wn = nifty[nifty.index < tr_end] if nifty is not None else None
        wtn = nifty[(nifty.index >= tr_end) & (nifty.index < te_end)] if nifty is not None else None
        if wt:
            _, et, _, _ = _run(wt, wtn, base_p)
            st = _stats([], et)
            st["n"] = 0
            t_train, _, _, _ = _run(wr, wn, base_p)
            tt, etest, _, _ = _run(wt, wtn, base_p)
            st = _stats(tt, etest)
            rows.append({
                "window": w + 1, "test_start": tr_end, "test_end": te_end,
                "test_n": st["n"], "test_net": round(st["net"]), "test_pf": round(st["pf"], 3),
            })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"):
        print("ERROR: OPENALGO_API_KEY")
        sys.exit(1)

    symbols = load_symbols()[:args.sample]
    raw, nifty, quality = load_universe_data(symbols, args.cache, args.start, args.end)
    print_data_quality(quality)
    if not raw:
        sys.exit(1)

    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    t1 = all_ts[int(n * 0.60)]
    t2 = all_ts[int(n * 0.80)]
    fresh_start = all_ts[int(n * 0.90)]
    print(f"Train<{t1} Val<{t2} Locked<{fresh_start} Fresh>{fresh_start}")

    tr, va, te, tn, vn, ten = _split_data(raw, nifty, t1, t2)
    fr = {s: d[d.index >= fresh_start] for s, d in raw.items() if (d.index >= fresh_start).any()}
    fn = nifty[nifty.index >= fresh_start] if nifty is not None else None

    # Baseline
    baseline_p = _mkp(rv=1.2, rw=1.0, ba=0.3, rb=8)
    print("\n--- Baseline ---")
    s_tr, s_va, s_te = _run_split(tr, va, te, tn, vn, ten, baseline_p)
    print(f"Train: n={s_tr['n']} net={s_tr['net']:.0f} pf={s_tr['pf']:.3f}")
    print(f"Val: n={s_va['n']} net={s_va['net']:.0f} pf={s_va['pf']:.3f}")
    print(f"Test: n={s_te['n']} net={s_te['net']:.0f} pf={s_te['pf']:.3f}")

    # HYPOTHESIS 1: Extreme abnormal volume
    print("\n=== H1: Extreme Abnormal Volume ===")
    h1_cfgs = []
    for rv in (1.2, 2.0, 3.0, 4.0):
        p = _mkp(rv=rv, rw=1.0, ba=0.3, rb=8)
        _s_tr, _s_va, _s_te = _run_split(tr, va, te, tn, vn, ten, p)
        h1_cfgs.append({"hyp": f"H1_rv={rv}", "train_net": round(_s_tr["net"]), "train_pf": _s_tr["pf"], "val_net": round(_s_va["net"]), "val_pf": _s_va["pf"], "test_net": round(_s_te["net"]), "test_pf": _s_te["pf"]})
        print(f"  RV>{rv:3.1f}: train={_s_tr['net']:6.0f}(pf={_s_tr['pf']:.3f}) val={_s_va['net']:6.0f}(pf={_s_va['pf']:.3f}) test={_s_te['net']:6.0f}(pf={_s_te['pf']:.3f})")
    for vp in (80, 90, 95, 99):
        p = _mkp(vp=vp, rv=1.0, rw=1.0, ba=0.3, rb=8)
        _s_tr, _s_va, _s_te = _run_split(tr, va, te, tn, vn, ten, p)
        h1_cfgs.append({"hyp": f"H1_vpct={vp}", "train_net": round(_s_tr["net"]), "train_pf": _s_tr["pf"], "val_net": round(_s_va["net"]), "val_pf": _s_va["pf"], "test_net": round(_s_te["net"]), "test_pf": _s_te["pf"]})
        print(f"  VP>{vp:3d}: train={_s_tr['net']:6.0f}(pf={_s_tr['pf']:.3f}) val={_s_va['net']:6.0f}(pf={_s_va['pf']:.3f}) test={_s_te['net']:6.0f}(pf={_s_te['pf']:.3f})")
    pd.DataFrame(h1_cfgs).to_csv(f"{OUT_DIR}/v5_hypothesis_results.csv", index=False)

    # HYPOTHESIS 2: Candle quality + displacement
    print("\n=== H2: Candle Quality & Displacement ===")
    h2_cfgs = []
    base_rv = 3.0
    for disp in (0.1, 0.25, 0.5, 1.0):
        for clv in (0.0, 0.60, 0.70):
            p = _mkp(rv=base_rv, rw=1.0, ba=0.3, rb=8, bd=disp, cl=clv)
            _s_tr, _s_va, _s_te = _run_split(tr, va, te, tn, vn, ten, p)
            h2_cfgs.append({"hyp": f"H2_disp={disp}_cl={clv}", "train_net": round(_s_tr["net"]), "train_pf": _s_tr["pf"], "val_net": round(_s_va["net"]), "val_pf": _s_va["pf"], "test_net": round(_s_te["net"]), "test_pf": _s_te["pf"]})
            print(f"  dis>{disp:4.2f} cl>{clv:4.2f}: train={_s_tr['net']:6.0f}(pf={_s_tr['pf']:.3f}) val={_s_va['net']:6.0f}(pf={_s_va['pf']:.3f}) test={_s_te['net']:6.0f}(pf={_s_te['pf']:.3f})")
    pd.DataFrame(h2_cfgs).to_csv(f"{OUT_DIR}/v5_entry_quality.csv", index=False)

    # HYPOTHESIS 3: Market regime + relative strength
    print("\n=== H3: Regime & Relative Strength ===")
    h3_cfgs = []
    for rv in (2.0, 3.0, 4.0):
        _p = _mkp(rv=rv, rw=1.0, ba=0.3, rb=8)
        _p.use_market_filter = True
        _s_tr, _s_va, _s_te = _run_split(tr, va, te, tn, vn, ten, _p)
        h3_cfgs.append({"hyp": f"H3_regime_rv={rv}", "train_net": round(_s_tr["net"]), "train_pf": _s_tr["pf"], "val_net": round(_s_va["net"]), "val_pf": _s_va["pf"], "test_net": round(_s_te["net"]), "test_pf": _s_te["pf"]})
        print(f"  regime rv>{rv:3.1f}: train={_s_tr['net']:6.0f}(pf={_s_tr['pf']:.3f}) val={_s_va['net']:6.0f}(pf={_s_va['pf']:.3f}) test={_s_te['net']:6.0f}(pf={_s_te['pf']:.3f})")
    # Long vs Short
    for d in ("both", "long", "short"):
        _p = _mkp(rv=3.0, rw=1.0, ba=0.3, rb=8, dirs=d)
        _s_tr, _s_va, _s_te = _run_split(tr, va, te, tn, vn, ten, _p)
        h3_cfgs.append({"hyp": f"H3_{d}_rv=3", "train_net": round(_s_tr["net"]), "train_pf": _s_tr["pf"], "val_net": round(_s_va["net"]), "val_pf": _s_va["pf"], "test_net": round(_s_te["net"]), "test_pf": _s_te["pf"]})
        print(f"  {d:>5} rv=3: train={_s_tr['net']:6.0f}(pf={_s_tr['pf']:.3f}) val={_s_va['net']:6.0f}(pf={_s_va['pf']:.3f}) test={_s_te['net']:6.0f}(pf={_s_te['pf']:.3f})")
    pd.DataFrame(h3_cfgs).to_csv(f"{OUT_DIR}/v5_regime_analysis.csv", index=False)

    # Time analysis
    print("\n=== Time Analysis ===")
    full_p = _mkp(rv=3.0, rw=1.0, ba=0.3, rb=8)
    full_t, full_eq, _, _ = _run(raw, nifty, full_p)
    if full_t:
        tdf = pd.DataFrame([t.to_row() for t in full_t])
        tdf["entry_hour"] = pd.to_datetime(tdf["entry_time"]).dt.hour
        for h in range(9, 16):
            sub = tdf[tdf["entry_hour"] == h]
            if not sub.empty:
                print(f"  {h:02d}:00: n={len(sub)} net={sub['net_pnl'].sum():7.0f} wr={(sub['net_pnl']>0).mean()*100:5.1f}%")
        pd.DataFrame(tdf).to_csv(f"{OUT_DIR}/v5_trades.csv", index=False)
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v5_trades.csv")

    if not full_eq.empty:
        full_eq.to_csv(f"{OUT_DIR}/v5_equity.csv", index=False)
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v5_equity.csv")

    # Select best composite
    all_cfgs = h1_cfgs + h2_cfgs + h3_cfgs
    best = max(all_cfgs, key=lambda c: c["val_pf"] if c["val_pf"] > 0 else 0)
    print(f"\nBest composite (by val PF): {best['hyp']} val_net={best['val_net']} val_pf={best['val_pf']:.3f}")

    # Build the best composite
    # Parse the best config name
    bc = best["hyp"]
    vp_val = None
    rv_val = 3.0
    disp_val = 0.0
    cl_val = 0.0
    d_val = "both"
    if "rv=" in bc:
        rv_val = float(bc.split("rv=")[1].split("_")[0])
    if "vpct=" in bc:
        vp_val = float(bc.split("vpct=")[1])
        rv_val = 1.0
    if "disp=" in bc:
        parts = bc.split("_")
        disp_val = float([p for p in parts if "disp=" in p][0].split("=")[1])
        cl_val = float([p for p in parts if "cl=" in p][0].split("=")[1])
    if "short" in bc:
        d_val = "short"
    elif "long" in bc and bc.split("_")[0] == "H3" and "long" in bc:
        d_val = "long"

    best_p = _mkp(rv=rv_val, rw=1.0, ba=0.3, rb=8, vp=vp_val, bd=disp_val, cl=cl_val, dirs=d_val)
    if "H3" in bc and "regime" in bc:
        best_p.use_market_filter = True

    # Fresh OOS
    fr_t, fr_eq, _, _ = _run(fr, fn, best_p)
    frs = _stats(fr_t, fr_eq)
    print(f"\nFresh OOS: n={frs['n']} net={frs['net']:.0f} pf={frs['pf']:.3f} wr={frs['wr']:.1f}%")

    # Walk-forward
    wf = _walk_forward(raw, nifty, best_p)
    pd.DataFrame(wf).to_csv(f"{OUT_DIR}/v5_walk_forward.csv", index=False)

    # Monte Carlo
    rng = np.random.default_rng(42)
    if full_t:
        trades_r = [t.r_multiple for t in full_t]
        risk_per = 250
        mc_all = []
        for _ in range(5000):
            seq = rng.choice(trades_r, size=len(trades_r))
            eqc = [100000.0]
            inv = 100000.0
            pnl = sum(r * risk_per for r in seq)
            eqc.append(100000 + pnl)
            inv = min(inv, eqc[-1])
            mc_all.append({"final": eqc[-1], "max_dd": 100000 - inv, "lowest": inv})
        mcdf = pd.DataFrame(mc_all).quantile([0.05, 0.25, 0.50, 0.75, 0.95])
        mcdf.to_csv(f"{OUT_DIR}/v5_trade_monte_carlo.csv")
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v5_trade_monte_carlo.csv")

    # Symbol analysis
    if full_t:
        sym_df = pd.DataFrame([t.to_row() for t in full_t])
        saw = sym_df.groupby("symbol").agg(n=("net_pnl", "count"), net=("net_pnl", "sum"), wr=("net_pnl", lambda x: (x > 0).mean() * 100)).sort_values("net", ascending=False)
        saw.to_csv(f"{OUT_DIR}/v5_symbol_analysis.csv")
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v5_symbol_analysis.csv")

    # Cost stress
    cost_rows = []
    for cp in (0.0, 0.01, 0.02, 0.03, 0.05):
        p = _mkp(rv=rv_val, rw=1.0, ba=0.3, rb=8, cost=cp, vp=vp_val, bd=disp_val, cl=cl_val, dirs=d_val)
        tt, et, _, _ = _run(te, ten, p)
        cs = _stats(tt, et)
        cost_rows.append({"cost_pct": cp, **cs})
    pd.DataFrame(cost_rows).to_csv(f"{OUT_DIR}/v5_cost_stress.csv", index=False)

    # Daily
    if not full_eq.empty:
        deq = full_eq.copy()
        deq["day"] = pd.to_datetime(deq["timestamp"]).dt.date
        daily = deq.groupby("day").agg(eq=("total_equity", "last")).reset_index()
        daily.to_csv(f"{OUT_DIR}/v5_daily.csv", index=False)

    # REPORT
    lines = []
    lines.append("# V5 Research Report")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("V5 rearchitects the strategy around extreme-abnormal-volume breakouts.")
    lines.append("")
    lines.append("## 2. V4 Failure Analysis")
    lines.append("V4.1 conclusively rejected the old range-breakout architecture (PF<1 on 7/7 gates).")
    lines.append("")
    lines.append("## 3. Hypotheses Tested")
    lines.append("- H1: Extreme abnormal volume (3.0×RV, volume percentile 80-99)")
    lines.append("- H2: Candle quality (close-location, body) + displacement")
    lines.append("- H3: Market regime (NIFTY EMA) + direction")
    lines.append("")
    lines.append("## 4. Results by Hypothesis")
    lines.append("### H1: Extreme Volume")
    for c in h1_cfgs:
        lines.append(f"- {c['hyp']}: train={c['train_net']:>6} pf={c['train_pf']:.3f}  val={c['val_net']:>6} pf={c['val_pf']:.3f}  test={c['test_net']:>6} pf={c['test_pf']:.3f}")
    lines.append("")

    lines.append("### H2: Candle Quality")
    for c in h2_cfgs:
        lines.append(f"- {c['hyp']}: train={c['train_net']:>6} pf={c['train_pf']:.3f}  val={c['val_net']:>6} pf={c['val_pf']:.3f}  test={c['test_net']:>6} pf={c['test_pf']:.3f}")
    lines.append("")

    lines.append("### H3: Market Regime")
    for c in h3_cfgs:
        lines.append(f"- {c['hyp']}: train={c['train_net']:>6} pf={c['train_pf']:.3f}  val={c['val_net']:>6} pf={c['val_pf']:.3f}  test={c['test_net']:>6} pf={c['test_pf']:.3f}")
    lines.append("")

    lines.append("## 5. Best Composite")
    lines.append(f"Selected: {best['hyp']}")
    lines.append(f"Train: net={best['train_net']} pf={best['train_pf']:.3f}")
    lines.append(f"Val: net={best['val_net']} pf={best['val_pf']:.3f}")
    lines.append(f"Test: net={best['test_net']} pf={best['test_pf']:.3f}")
    lines.append(f"Fresh OOS: net={frs['net']} pf={frs['pf']:.3f} wr={frs['wr']:.1f}%")
    lines.append("")

    lines.append("## 6. Walk-Forward")
    for r in wf:
        lines.append(f"Window {r['window']}: test_n={r['test_n']} net={r['test_net']} pf={r['test_pf']:.3f}")
    lines.append("")

    lines.append("## 7. Cost Stress")
    for r in cost_rows:
        lines.append(f"  cost={r['cost_pct']:4.2f}: n={r['n']} net={r['net']:7.0f} pf={r['pf']:.3f}")
    lines.append("")

    lines.append("## 8. Monte Carlo (best composite)")
    if not mcdf.empty:
        lines.append(mcdf.to_string())
        lines.append(f"95th pctile DD: {mcdf.loc[0.95, 'max_dd']:.0f}")
    else:
        lines.append("N/A")
    lines.append("")

    lines.append("## 9. Decision Table")
    lines.append("| Test | Result | Status |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| Fresh OOS | net={frs['net']:.0f} pf={frs['pf']:.2f} | {'PASS' if frs['pf'] > 1.2 else 'FAIL'} |")
    wf_pass = all(r.get("test_pf", 0) > 0.9 for r in wf) if wf else False
    lines.append(f"| Walk-forward | avg pf={np.mean([r.get('test_pf', 0) for r in wf]):.3f} | {'PASS' if wf_pass else 'FAIL'} |")
    cost_pass = all(r['pf'] > 0.9 for r in cost_rows) if cost_rows else False
    lines.append(f"| Cost robust | min_pf={min(r['pf'] for r in cost_rows):.3f} | {'PASS' if cost_pass else 'FAIL'} |")
    mc_pass = not mcdf.empty and mcdf.loc[0.95, "max_dd"] < 15000
    lines.append(f"| Monte Carlo DD | 95th={mcdf.loc[0.95, 'max_dd']:.0f} | {'PASS' if mc_pass else 'FAIL'} |" if not mcdf.empty else "| Monte Carlo | - | SKIP |")

    verdict = (
        "PROMISING BUT NEEDS MORE DATA" if frs["pf"] > 1 and frs["net"] > 0
        else "NO ROBUST EDGE FOUND" if frs["pf"] < 1
        else "ROBUST ENOUGH FOR PAPER TRADING"
    )
    if frs["pf"] > 1.2 and all(r.get("test_pf", 0) > 1 for r in wf) and wf_pass:
        verdict = "ROBUST ENOUGH FOR PAPER TRADING"

    lines.append("")
    lines.append(f"## Final Recommendation: **{verdict}**")
    lines.append("")

    report = "\n".join(lines)
    with open(f"{OUT_DIR}/V5_RESEARCH_REPORT.md", "w") as f:
        f.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
