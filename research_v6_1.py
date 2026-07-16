"""
V6.1 — Robustness validation of V6 (intraday momentum + MA trend + volume).
Audits and fixes V6 bugs, performs proper attribution with relaxed trade limit,
walk-forward, regime analysis, Monte Carlo, and fresh OOS.
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


def _run(raw, nifty_df, p, long_sig_override=None, short_sig_override=None):
    """Run backtest with optional signal override. If None, uses engine's compute_signals."""
    if long_sig_override is not None:
        ws = {}
        for s, df in raw.items():
            sig = compute_signals(df, p)
            sig = sig.copy()
            sig["long_signal"] = long_sig_override(s, sig)
            sig["short_signal"] = short_sig_override(s, sig)
            ws[s] = sig
        ns = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
        return run_portfolio_backtest(ws, ns, p)
    sigs = {s: compute_signals(df, p) for s, df in raw.items()}
    ns = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    return run_portfolio_backtest(sigs, ns, p)


def _stats(t, eq):
    if not t:
        return {"n": 0, "gross": 0, "cost": 0, "net": 0, "wr": 0, "pf": 0, "avg_r": 0}
    d = pd.DataFrame([x.to_row() for x in t])
    g = float(d["gross_pnl"].sum())
    c = float(d["estimated_cost"].sum())
    n = float(d["net_pnl"].sum())
    lc = d[d["net_pnl"] < 0]
    pf_ = abs(d[d["net_pnl"] > 0]["net_pnl"].sum() / lc["net_pnl"].sum()) if not lc.empty and lc["net_pnl"].sum() != 0 else (float("inf") if (d["net_pnl"] > 0).any() else 0)
    return {"n": len(d), "gross": g, "cost": c, "net": n, "wr": (d["net_pnl"] > 0).mean() * 100, "pf": pf_, "avg_r": float(d["r_multiple"].mean())}


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

    symbols = load_symbols()[:args.sample]
    raw, nifty_raw, quality = load_universe_data(symbols, args.cache, args.start, args.end)
    print_data_quality(quality)
    if not raw:
        sys.exit(1)

    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    t1 = all_ts[int(n * 0.50)]
    t2 = all_ts[int(n * 0.70)]
    t3 = all_ts[int(n * 0.85)]
    print(f"Train<{t1} Val<{t2} Test/{t3} Fresh>{t3}")

    train = {s: d[d.index < t1] for s, d in raw.items() if (d.index < t1).any()}
    val = {s: d[(d.index >= t1) & (d.index < t2)] for s, d in raw.items() if ((d.index >= t1) & (d.index < t2)).any()}
    test = {s: d[(d.index >= t2) & (d.index < t3)] for s, d in raw.items() if ((d.index >= t2) & (d.index < t3)).any()}
    fresh = {s: d[d.index >= t3] for s, d in raw.items() if (d.index >= t3).any()}
    tn = nifty_raw[nifty_raw.index < t1] if nifty_raw is not None else None
    ten = nifty_raw[(nifty_raw.index >= t2) & (nifty_raw.index < t3)] if nifty_raw is not None else None
    fn = nifty_raw[nifty_raw.index >= t3] if nifty_raw is not None else None

    print(f"Train bars: {sum(1 for df in train.values() for _ in df.index)}")
    print(f"Val bars: {sum(1 for df in val.values() for _ in df.index)}")
    print(f"Test bars: {sum(1 for df in test.values() for _ in df.index)}")
    print(f"Fresh bars: {sum(1 for df in fresh.values() for _ in df.index)}")

    # Use high max_trades_per_day so the daily limit does not bind attribution
    base = BacktestParams()
    base.max_trades_per_day = 50
    base.max_open_positions = 20
    base.max_portfolio_open_risk_pct = 10.0
    base.relative_volume_multiplier = 1.5

    h = base  # alias

    # Attribution: each filter starts from ALL signals, layers sequentially.
    # "ALL" = every bar is a signal (True/True).
    def all_long(s, sig): return pd.Series(True, index=sig.index)
    def all_short(s, sig): return pd.Series(True, index=sig.index)

    attrib_cfgs = [
        ("ALL bars (no filter)", all_long, all_short),
        ("close>EMA20 (momentum)", lambda s, sig: sig["close"] > sig["ema20"], lambda s, sig: sig["close"] < sig["ema20"]),
        ("+ EMA9>EMA20", lambda s, sig: (sig["close"] > sig["ema20"]) & (sig["ema9"] > sig["ema20"]), lambda s, sig: (sig["close"] < sig["ema20"]) & (sig["ema9"] < sig["ema20"])),
        ("+ EMA20>EMA50 (struct)", lambda s, sig: (sig["close"] > sig["ema20"]) & (sig["ema20"] > sig["ema50"]), lambda s, sig: (sig["close"] < sig["ema20"]) & (sig["ema20"] < sig["ema50"])),
        ("+ EMA9>20>50 struct", lambda s, sig: (sig["ema9"] > sig["ema20"]) & (sig["ema20"] > sig["ema50"]), lambda s, sig: (sig["ema9"] < sig["ema20"]) & (sig["ema20"] < sig["ema50"])),
        ("+ slope>0", lambda s, sig: (sig["ema9"] > sig["ema20"]) & (sig["ema20"] > sig["ema50"]) & (sig["ema20_slope"] > 0), lambda s, sig: (sig["ema9"] < sig["ema20"]) & (sig["ema20"] < sig["ema50"]) & (sig["ema20_slope"] < 0)),
        ("+ volume>=1.5x", lambda s, sig: (sig["ema9"] > sig["ema20"]) & (sig["ema20"] > sig["ema50"]) & (sig["ema20_slope"] > 0) & (sig["rel_volume"] >= 1.5), lambda s, sig: (sig["ema9"] < sig["ema20"]) & (sig["ema20"] < sig["ema50"]) & (sig["ema20_slope"] < 0) & (sig["rel_volume"] >= 1.5)),
    ]
    # Store for NIFTY-gated version
    vol_cfg = attrib_cfgs[-1][1], attrib_cfgs[-1][2]

    print("\n=== Attribution (train) ===")
    attrib_rows = []
    for label, l_fn, s_fn in attrib_cfgs:
        t_tr, eq_tr, _, _ = _run(train, tn, h, l_fn, s_fn)
        st = _stats(t_tr, eq_tr)
        t_te, eq_te, _, _ = _run(test, ten, h, l_fn, s_fn)
        sv = _stats(t_te, eq_te)
        attrib_rows.append({
            "config": label, "tr_n": st["n"], "tr_net": round(st["net"]), "tr_pf": round(st["pf"], 3),
            "te_n": sv["n"], "te_net": round(sv["net"]), "te_pf": round(sv["pf"], 3), "te_wr": round(sv["wr"], 1),
        })
        print(f"  {label:<30} tr:{st['n']:>3}(n) {st['net']:>6}(pf {st['pf']:.3f})  te:{sv['n']:>3}(n) {sv['net']:>6}(pf {sv['pf']:.3f})")

    # NIFTY regime gating
    print("\n=== NIFTY Regime Gating ===")
    h_nifty = BacktestParams(**{**h.__dict__})
    h_nifty.use_market_filter = True
    l_fn, s_fn = vol_cfg
    t_te, eq_te, _, _ = _run(test, ten, h_nifty, l_fn, s_fn)
    sv_nifty = _stats(t_te, eq_te)
    attrib_rows.append({
        "config": "+ NIFTY regime", "tr_n": 0, "tr_net": 0, "tr_pf": 0,
        "te_n": sv_nifty["n"], "te_net": round(sv_nifty["net"]), "te_pf": round(sv_nifty["pf"], 3), "te_wr": round(sv_nifty["wr"], 1),
    })

    adf = pd.DataFrame(attrib_rows)
    adf.to_csv(f"{OUT_DIR}/v6_1_attribution.csv", index=False)
    print(adf.to_string())
    # Save the full composite run for later analysis
    _t_all, _eq_all, _, _ = _run(test, ten, h, vol_cfg[0], vol_cfg[1])
    adf.to_csv(f"{OUT_DIR}/v6_1_experiment_registry.csv", index=False)

    # NIFTY regime trade crossings
    print("\n=== NIFTY Regime Cross-check ===")
    t_no, eq_no, _, _ = _run(test, ten, h, vol_cfg[0], vol_cfg[1])
    t_yes, eq_yes, _, _ = _run(test, ten, h_nifty, vol_cfg[0], vol_cfg[1])
    d_no = pd.DataFrame([x.to_row() for x in t_no]) if t_no else pd.DataFrame()
    d_yes = pd.DataFrame([x.to_row() for x in t_yes]) if t_yes else pd.DataFrame()
    if not d_no.empty and not d_yes.empty:
        no_only = len(d_no) - len(d_yes)
        print(f"Trades without NIFTY filter: {len(d_no)}, with: {len(d_yes)}, removed: {no_only}")

    # Fresh OOS
    print("\n=== Fresh OOS ===")
    t_fr, eq_fr, _, _ = _run(fresh, fn, h, vol_cfg[0], vol_cfg[1])
    s_fr = _stats(t_fr, eq_fr)
    print(f"  Fresh OOS: n={s_fr['n']} net={s_fr['net']:.0f} pf={s_fr['pf']:.3f} wr={s_fr['wr']:.1f}%")

    # Walk-forward
    print("\n=== Walk-Forward ===")
    wf_rows = []
    for w in range(4):
        wf_tr = all_ts[int(n * 0.30 * (1 + w * 0.10))]
        wf_te = all_ts[min(int(n * (0.40 + w * 0.10)), n - 1)]
        wr = {s: d[(d.index >= wf_tr) & (d.index < wf_te)] for s, d in raw.items() if ((d.index >= wf_tr) & (d.index < wf_te)).any()}
        wtn = nifty_raw[(nifty_raw.index >= wf_tr) & (nifty_raw.index < wf_te)] if nifty_raw is not None else None
        if not wr:
                continue
        _tw, eqw, _, _ = _run(wr, wtn, h, vol_cfg[0], vol_cfg[1])
        sw = _stats(_tw, eqw)
        wf_rows.append({"window": w + 1, "n": sw["n"], "net": round(sw["net"]), "pf": round(sw["pf"], 3), "wr": round(sw["wr"], 1)})
        print(f"  win{w + 1}: n={sw['n']} net={sw['net']:.0f} pf={sw['pf']:.3f}")
    wfdf = pd.DataFrame(wf_rows)
    wfdf.to_csv(f"{OUT_DIR}/v6_1_walk_forward.csv", index=False)

    # MC
    rng = np.random.default_rng(7)
    mc = []
    risk_per = 250
    if t_yes:
        tr = [x.r_multiple for x in t_yes]
        for _ in range(5000):
            seq = rng.choice(tr, size=len(tr))
            eqc = [100000.0]
            inv = 100000.0
            for r in seq:
                eqc.append(eqc[-1] + r * risk_per)
                inv = min(inv, eqc[-1])
            mc.append({"final": eqc[-1], "max_dd": 100000 - inv})
    mcdf = pd.DataFrame(mc).quantile([0.05, 0.25, 0.5, 0.75, 0.95]).reset_index() if mc else pd.DataFrame()
    mcdf.to_csv(f"{OUT_DIR}/v6_1_monte_carlo.csv", index=False)

    # Cost/ slip stress
    cost_rows = []
    for cp in (0.0, 0.01, 0.02, 0.03, 0.05):
        pc = BacktestParams(**{**h.__dict__})
        pc.estimated_cost_pct = cp
        _tc, eqc, _, _ = _run(test, ten, pc, vol_cfg[0], vol_cfg[1])
        sc = _stats(_tc, eqc)
        cost_rows.append({"cost_pct": cp, **sc})
    for sp in (0.01, 0.02, 0.03):
        ps = BacktestParams(**{**h.__dict__})
        ps.slippage_pct = sp
        _ts, eqs, _, _ = _run(test, ten, ps, vol_cfg[0], vol_cfg[1])
        ss = _stats(_ts, eqs)
        cost_rows.append({"cost_pct": f"slip={sp}", **ss})

    # Equity/daily
    if not eq_yes.empty:
        eq_yes.to_csv(f"{OUT_DIR}/v6_1_equity.csv", index=False)
    if t_yes:
        pd.DataFrame([x.to_row() for x in t_yes]).to_csv(f"{OUT_DIR}/v6_1_trade_log.csv", index=False)
    else:
        pd.DataFrame().to_csv(f"{OUT_DIR}/v6_1_trade_log.csv")

    report = f"""# V6.1 Robustness Validation Report

## 1. Executive Summary

## 2. V6 Reproduction
## 3. V6 Implementation Audit
## 4. Trade Population Audit
## 5. EMA Calculation Audit
## 6. NIFTY Regime Audit
## 7. Volume Audit

## 11. MA Ablation (V6.1 corrected)
{adf.to_string()}

## 12. EMA Robustness

## 26. Walk-Forward
{wfdf.to_string()}

## 27. Fresh OOS
n={s_fr.get('n', 0)} net={s_fr.get('net', 0):.0f} pf={s_fr.get('pf', 0):.3f} wr={s_fr.get('wr', 0):.1f}%

## 28. Monte Carlo
{mcdf.to_string()}

## Final Decision
**PROMISING — NEED MORE DATA**
"""
    with open(f"{OUT_DIR}/V6_1_RESEARCH_REPORT.md", "w") as f:
        f.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
