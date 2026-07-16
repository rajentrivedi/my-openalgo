"""
VWROC momentum strategy - robustness validation harness.

Tests whether the SBIN volume-weighted momentum edge survives stress testing:
1. Train/test split (optimize 2021-24, validate 2024-26)
2. Rolling walk-forward (per-window re-optimization, IS vs OOS efficiency)
3. Cross-symbol validation (8 liquid NSE stocks)
4. Parameter sensitivity (neighborhood around optimum)
5. Entry/exit delay test (1-2 day execution lag)
6. Monte Carlo trade shuffling (confidence intervals on return)
7. Robustness scorecard

Usage:
    venv/bin/python backtesting/vwroc_momentum/vwroc_robustness.py
    venv/bin/python backtesting/vwroc_momentum/vwroc_robustness.py --skip-mc
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

import yfinance as yf

script_dir = Path(__file__).resolve().parent

INIT_CASH = 1_000_000
FEES = 0.00111
FIXED_FEES = 20
ALLOCATION = 0.75
YEARS = 5
LOOKBACK = 15
ENTRY = 0.5
EXIT = -1.0

# Parameter grid for optimization / sensitivity
LOOKBACKS = [10, 15, 20]
ENTRIES = [0.3, 0.5, 0.8]
EXITS = [-1.5, -1.0, -0.5]

NSE_BASKET = [
    "SBIN.NS", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "TCS.NS", "INFY.NS", "KOTAKBANK.NS", "AXISBANK.NS",
]

parser = argparse.ArgumentParser(description="VWROC robustness validation")
parser.add_argument("--skip-mc", action="store_true", help="skip Monte Carlo (slow)")
args = parser.parse_args()


_CACHE = {}

def fetch(symbol: str):
    if symbol in _CACHE:
        return _CACHE[symbol]
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=365 * YEARS)
    df = yf.download(symbol, start=start_date, end=end_date, interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    _CACHE[symbol] = df
    return df


def vwroc(close: pd.Series, volume: pd.Series, lookback: int) -> pd.Series:
    price_chg = close.diff()
    return (price_chg * volume).rolling(lookback).sum() / volume.rolling(lookback).sum() / close * 100.0


def signals(close, volume, lookback, entry, exit):
    v = vwroc(close, volume, lookback)
    buy_raw = (v > entry) & (v.shift(1) <= entry)
    sell_raw = (v < exit) & (v.shift(1) >= exit)
    entries = buy_raw.fillna(False).copy()
    exits = sell_raw.fillna(False).copy()
    active = False
    for i in range(len(entries)):
        if active:
            entries.iloc[i] = False
        if entries.iloc[i] and not active:
            active = True
        if exits.iloc[i]:
            active = False
    return entries, exits, v


def backtest(close, volume, lookback, entry, exit):
    e, x, _ = signals(close, volume, lookback, entry, exit)
    if e.sum() == 0:
        return None
    pf = vbt.Portfolio.from_signals(
        close, e, x,
        init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
        fees=FEES, fixed_fees=FIXED_FEES, direction="longonly",
        min_size=1, size_granularity=1, freq="1D",
    )
    return pf


def optimize(df: pd.DataFrame):
    """Grid-search in-sample. Returns (best_params, best_sharpe, results_df)."""
    close, volume = df["Close"], df["Volume"]
    rows = []
    for lb in LOOKBACKS:
        for en in ENTRIES:
            for ex in EXITS:
                if ex >= en:
                    continue
                pf = backtest(close, volume, lb, en, ex)
                if pf is None:
                    continue
                rows.append({
                    "lookback": lb, "entry": en, "exit": ex,
                    "return": pf.total_return(), "sharpe": pf.sharpe_ratio(),
                    "trades": pf.trades.count(),
                })
    if not rows:
        return None, None, pd.DataFrame()
    grid = pd.DataFrame(rows)
    grid_valid = grid[grid["sharpe"].notna()]
    if grid_valid.empty:
        return None, None, grid
    best = grid_valid.sort_values("sharpe", ascending=False).iloc[0]
    return best, grid_valid, grid


def walk_forward():
    """Rolling 2y train / 6mo test walk-forward, 6-month steps."""
    df = fetch("SBIN.NS")
    close, volume = df["Close"], df["Volume"]
    results = []
    train_days, test_days, step = 504, 126, 126
    start = 0
    while start + train_days + test_days <= len(close):
        train = df.iloc[start:start + train_days]
        test = df.iloc[start + train_days:start + train_days + test_days]
        best, grid, _ = optimize(train)
        if best is None:
            start += step
            continue
        pf = backtest(test["Close"], test["Volume"], int(best["lookback"]), float(best["entry"]), float(best["exit"]))
        oos_ret = pf.total_return() if pf is not None else np.nan
        oos_sharpe = pf.sharpe_ratio() if pf is not None else np.nan
        oos_trades = pf.trades.count() if pf is not None else 0
        results.append({
            "train_from": train.index[0].date(), "train_to": train.index[-1].date(),
            "test_from": test.index[0].date(), "test_to": test.index[-1].date(),
            "best_lb": int(best["lookback"]), "best_entry": float(best["entry"]),
            "best_exit": float(best["exit"]), "is_sharpe": float(best["sharpe"]),
            "is_return": float(best["return"]), "oos_return": oos_ret,
            "oos_sharpe": oos_sharpe, "oos_trades": oos_trades,
        })
        start += step

    wf = pd.DataFrame(results)
    print("\n" + "=" * 80)
    print("WALK-FORWARD ANALYSIS (2y train / 6mo test, rolling 6mo)")
    print("=" * 80)
    if wf.empty:
        print("No windows generated (insufficient data).")
        return None
    print(wf.to_string(index=False))
    wf_oos = wf.dropna(subset=["oos_return"])
    if not wf_oos.empty:
        mean_is = wf_oos["is_return"].mean()
        mean_oos = wf_oos["oos_return"].mean()
        wfe = mean_oos / mean_is if mean_is != 0 else np.nan
        pos = (wf_oos["oos_return"] > 0).mean()
        print(f"\nAvg in-sample return : {mean_is * 100:.1f}%")
        print(f"Avg OOS return       : {mean_oos * 100:.1f}%")
        print(f"Walk-forward efficiency (OOS/IS): {wfe * 100:.1f}%  (>50% acceptable, >70% good)")
        print(f"OOS windows profitable: {pos * 100:.0f}%")
        print(f"OOS total trades     : {int(wf_oos['oos_trades'].sum())}")
        wf_oos.to_csv(script_dir / "SBIN_vwroc_walkforward.csv", index=False)
    return wf


def train_test_split():
    """Simple 70/30 split: optimize on first 70%, validate on last 30%."""
    df = fetch("SBIN.NS")
    split = int(len(df) * 0.7)
    print("\n" + "=" * 80)
    print("TRAIN/TEST SPLIT (70/30 SBIN.NS)")
    print("=" * 80)
    print(f"Split date: {df.index[split].date()} (train {len(df) - split} bars unseen)")
    best, grid, _ = optimize(df.iloc[:split])
    if best is None:
        print("No in-sample config tradable.")
        return
    print(f"In-sample best config: lb={int(best['lookback'])} entry={best['entry']:.1f} "
          f"exit={best['exit']:.1f}  ret={best['return'] * 100:.1f}%  sharpe={best['sharpe']:.2f}  trades={int(best['trades'])}")
    test = df.iloc[split:]
    pf = backtest(test["Close"], test["Volume"], int(best["lookback"]), float(best["entry"]), float(best["exit"]))
    if pf is None:
        print("Out-of-sample: no trades. FAIL - config does not trade OOS.")
        return
    buy_hold = float(test["Close"].iloc[-1] / test["Close"].iloc[0] - 1) * 100
    print(f"Out-of-sample return : {pf.total_return() * 100:.1f}%   (buy&hold {buy_hold:.1f}%)")
    print(f"Out-of-sample sharpe : {pf.sharpe_ratio():.2f}")
    print(f"Out-of-sample trades : {pf.trades.count()}")
    print(f"OOS / IS return ratio: {pf.total_return() / best['return'] * 100:.0f}%  (>50% acceptable)")
    pf.trades.records_readable.to_csv(script_dir / "SBIN_vwroc_oos_trades.csv", index=False)


def cross_symbol():
    """Run the FIXED default config on 8 liquid NSE stocks. True edge works across symbols."""
    print("\n" + "=" * 80)
    print("CROSS-SYMBOL VALIDATION (fixed config: lb=15 entry=0.5 exit=-1.0 alloc=0.75)")
    print("=" * 80)
    rows = []
    for sym in NSE_BASKET:
        try:
            df = fetch(sym)
        except Exception as e:
            print(f"  {sym}: fetch failed ({e})")
            continue
        if df.empty or len(df) < 50:
            print(f"  {sym}: no data (skipped)")
            continue
        pf = backtest(df["Close"], df["Volume"], LOOKBACK, ENTRY, EXIT)
        bh = float(df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        if pf is None:
            rows.append({"symbol": sym, "return": np.nan, "sharpe": np.nan, "max_dd": np.nan,
                         "trades": 0, "buy_hold": bh})
            print(f"  {sym:<14} NO TRADES  (bh {bh:+.1f}%)")
        else:
            rows.append({"symbol": sym, "return": pf.total_return(), "sharpe": pf.sharpe_ratio(),
                         "max_dd": pf.max_drawdown(), "trades": pf.trades.count(), "buy_hold": bh})
            print(f"  {sym:<14} ret={pf.total_return() * 100:+6.1f}%  sharpe={pf.sharpe_ratio():+5.2f}  "
                  f"dd={pf.max_drawdown() * -100:5.1f}%  trades={pf.trades.count():>3}   (bh {bh:+.1f}%)")
    out = pd.DataFrame(rows)
    tradable = out[out["trades"] >= 5]
    pos = (tradable["return"] > 0).mean() if len(tradable) else np.nan
    print(f"\nSymbols with >=5 trades: {len(tradable)} of {len(out)}")
    print(f"% profitable (>=5 trades): {pos * 100:.0f}%   (>60% = genuine edge)")
    avg_ret = tradable["return"].mean() if len(tradable) else np.nan
    print(f"Avg return (>=5 trades): {avg_ret * 100:.1f}%")
    out.to_csv(script_dir / "SBIN_vwroc_crosssymbol.csv", index=False)
    return out


def sensitivity():
    """Grid near the optimum. Robust: broad plateau of profitability, not a narrow spike."""
    df = fetch("SBIN.NS")
    print("\n" + "=" * 80)
    print("PARAMETER SENSITIVITY (neighborhood of lb=15 entry=0.5 exit=-1.0)")
    print("=" * 80)
    rows = []
    for lb in range(10, 26):
        for en in [0.3, 0.5, 0.8, 1.2]:
            for ex in [-0.5, -0.8, -1.0, -1.5]:
                if ex >= en:
                    continue
                pf = backtest(df["Close"], df["Volume"], lb, en, ex)
                rows.append({
                    "lookback": lb, "entry": en, "exit": ex,
                    "return": pf.total_return() if pf else np.nan,
                    "sharpe": pf.sharpe_ratio() if pf else np.nan,
                    "trades": pf.trades.count() if pf else 0,
                })
    sens = pd.DataFrame(rows)
    sens_valid = sens[sens["trades"] >= 5]
    frac_pos = (sens_valid["return"] > 0).mean() if len(sens_valid) else np.nan
    print(f"Configs with >=5 trades: {len(sens_valid)} of {len(sens)}")
    print(f"% profitable: {frac_pos * 100:.0f}%   (>80% = robust plateau)")
    print(f"Avg return: {sens_valid['return'].mean() * 100:.1f}%  "
          f"median: {sens_valid['return'].median() * 100:.1f}%  "
          f"min: {sens_valid['return'].min() * 100:.1f}%  "
          f"max: {sens_valid['return'].max() * 100:.1f}%")
    if not sens_valid.empty:
        print("\nReturn by lookback (all thresholds pooled):")
        by_lb = sens_valid.groupby("lookback")["return"].agg(["mean", lambda s: (s > 0).mean()])
        by_lb.columns = ["mean_return", "pct_profitable"]
        print(by_lb.to_string())
    sens.to_csv(script_dir / "SBIN_vwroc_sensitivity.csv", index=False)
    return sens


def delay_test():
    """Delay entry/exit signals by 1-2 days to test execution timing sensitivity."""
    df = fetch("SBIN.NS")
    close, volume = df["Close"], df["Volume"]
    e, x, _ = signals(close, volume, LOOKBACK, ENTRY, EXIT)
    print("\n" + "=" * 80)
    print("ENTRY/EXIT DELAY TEST (execution slippage = signal seen 1-2 days late)")
    print("=" * 80)
    for ed in (0, 1, 2):
        for xd in (0, 1, 2):
            ee = e.shift(ed).fillna(False).astype(bool)
            xx = x.shift(xd).fillna(False).astype(bool)
            pf = vbt.Portfolio.from_signals(
                close, ee, xx, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
                fees=FEES, fixed_fees=FIXED_FEES, direction="longonly",
                min_size=1, size_granularity=1, freq="1D",
            )
            print(f"  entry+{ed}d, exit+{xd}d: ret={pf.total_return() * 100:+7.1f}%  "
                  f"sharpe={pf.sharpe_ratio():+.2f}  trades={pf.trades.count()}")
    return


def monte_carlo():
    """Shuffle trade P&L order; estimate distribution of final return and max DD.

    Returns (was_run, pct5_positive)."""
    if args.skip_mc:
        return False, False
    df = fetch("SBIN.NS")
    pf = backtest(df["Close"], df["Volume"], LOOKBACK, ENTRY, EXIT)
    if pf is None:
        print("\nMONTE CARLO: no trades to shuffle.")
        return True, False
    trades = pf.trades.records_readable
    pnl = trades["PnL"].fillna(0).values
    print("\n" + "=" * 80)
    print(f"MONTE CARLO TRADE SHUFFLE ({len(pnl)} trades, 2000 sims)")
    print("=" * 80)
    rng = np.random.default_rng(42)
    final_returns, max_dds = [], []
    for _ in range(2000):
        shuff = rng.permutation(pnl)
        equity = np.cumsum(shuff) + INIT_CASH
        peak = np.maximum.accumulate(equity)
        final_returns.append(equity[-1] / INIT_CASH - 1)
        max_dds.append(((equity - peak) / peak).min())
    rets = pd.Series(final_returns)
    dds = pd.Series(max_dds)
    print(f"  Return mean   : {rets.mean() * 100:+.1f}%")
    print(f"  Return 5th pct: {rets.quantile(0.05) * 100:+.1f}%   (want > 0)")
    print(f"  Return 95th pct: {rets.quantile(0.95) * 100:+.1f}%")
    print(f"  % sims profitable: {(rets > 0).mean() * 100:.0f}%")
    print(f"  MaxDD mean     : {dds.mean() * 100:+.1f}%")
    print(f"  MaxDD worst 5% : {dds.quantile(0.05) * 100:+.1f}%   (be prepared for this)")
    print(f"  (Note: shuffling removes ordering luck, but keeps the same trade set - "
          f"a {len(pnl)}-trade sample is small, treat as indicative)")
    return True, bool(rets.quantile(0.05) > 0)


def scorecard(eval_dict):
    print("\n" + "=" * 80)
    print("ROBUSTNESS SCORECARD")
    print("=" * 80)
    checks = [
        ("OOS window profitable (WF)", eval_dict.get("wf_pos", False)),
        ("OOS/IS ratio > 50%", eval_dict.get("oos_ratio", False)),
        ("Cross-symbol >60% profitable", eval_dict.get("cross_pos", False)),
        ("Param sensitivity >80% profitable", eval_dict.get("sens_pos", False)),
        ("Delay test: baseline (0/0) profitable", eval_dict.get("delay_base", False)),
        ("Monte Carlo 5th pctl > 0", eval_dict.get("mc_5pct", False)),
    ]
    passed = sum(1 for _, ok in checks if ok)
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\nScore: {passed}/{len(checks)}")
    if passed >= 5:
        print("Verdict: ROBUST - candidate for live consideration")
    elif passed >= 3:
        print("Verdict: MODERATE - proceed with caution; refine weak dimensions")
    else:
        print("Verdict: FAILS robustness - do not trade live without changes")


def main():
    wf = walk_forward()
    train_test_split()

    cross = cross_symbol()
    sens = sensitivity()
    delay_results = delay_test()
    mc_ran, mc_5 = monte_carlo()

    # Build scorecard booleans
    wf_pos = False
    if wf is not None and not wf.dropna(subset=["oos_return"]).empty:
        wf_pos = bool((wf.dropna(subset=["oos_return"])["oos_return"] > 0).mean() >= 0.5)

    oos_ratio = False
    # Recompute 70/30 OOS/IS ratio
    df_sbin = fetch("SBIN.NS")
    split = int(len(df_sbin) * 0.7)
    best, _, _ = optimize(df_sbin.iloc[:split])
    if best is not None:
        pf = backtest(df_sbin.iloc[split:]["Close"], df_sbin.iloc[split:]["Volume"], int(best["lookback"]), float(best["entry"]), float(best["exit"]))
        if pf is not None and best["return"] != 0:
            oos_ratio = bool(pf.total_return() / best["return"] > 0.5)

    cross_pos = False
    if cross is not None:
        tradable = cross[cross["trades"] >= 5]
        if len(tradable) >= 3:
            cross_pos = bool((tradable["return"] > 0).mean() >= 0.6)

    sens_pos = False
    if sens is not None:
        sv = sens[sens["trades"] >= 5]
        if len(sv) >= 10:
            sens_pos = bool((sv["return"] > 0).mean() >= 0.8)

    delay_base = False
    df2 = fetch("SBIN.NS")
    e, x, _ = signals(df2["Close"], df2["Volume"], LOOKBACK, ENTRY, EXIT)
    pf0 = vbt.Portfolio.from_signals(
        df2["Close"], e, x, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
        fees=FEES, fixed_fees=FIXED_FEES, direction="longonly",
        min_size=1, size_granularity=1, freq="1D")
    delay_base = bool(pf0.total_return() > 0)

    scorecard({
        "wf_pos": wf_pos, "oos_ratio": oos_ratio, "cross_pos": cross_pos,
        "sens_pos": sens_pos, "delay_base": delay_base, "mc_5pct": mc_5,
    })


if __name__ == "__main__":
    main()