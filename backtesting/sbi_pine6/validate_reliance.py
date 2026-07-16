"""Validate the SBIN gap-continuation pattern on RELIANCE (same period, same harness).

Works from a temp copy of the DuckDB so the running app's lock can't block us.
Checks, all with 0.05%/side costs, 20% notional per trade:
  A. The exact SBIN config: short on gap < -0.05% after a red day.
  B. RELIANCE mirror (fade):  short on gap > +0.05% after a green day.
  C. RELIANCE bounce:         long  on gap < -0.05% after a red day.
  D/E. Entry-time robustness + half-sample split for the best pattern.
"""

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backtesting" / "sbi_pine6"))

# Work from a temp copy so the running app's DuckDB lock can't interfere.
_tmp = Path(tempfile.mkdtemp(prefix="hist_")) / "historify.duckdb"
shutil.copy2(ROOT / "db" / "historify.duckdb", _tmp)

import backtest  # noqa: E402

backtest.DB_PATH = _tmp
from backtest import atr, load_1m, resample_5m  # noqa: E402

INIT, RN, FEES = 1_000_000, 0.20, 0.0005


def day_trades(symbol, entry_min, side, gap_op, gap_thresh, prev_red, sl_mult,
               exit_min=15 * 60 + 15):
    """side=1 long / -1 short. gap_op '<' -> gap < gap_thresh, '>' -> gap > gap_thresh.
    prev_red: require yesterday red (True) or green (False)."""
    df1 = load_1m(symbol)
    df5 = resample_5m(df1)
    a5 = atr(df5["high"], df5["low"], df5["close"], 14)
    daily = df1.resample("1D").agg({"open": "first", "close": "last"}).dropna()
    daily["prev_close"] = daily["close"].shift(1)
    daily["prev_day_up"] = daily["close"].shift(1) > daily["open"].shift(1)
    daily["gap"] = (daily["open"] / daily["prev_close"] - 1) * 100
    daily_map = {ts.date(): r for ts, r in daily.iterrows()}
    df5_map = {ts.normalize().date(): ts for ts in df5.index}

    trades = []
    for d in pd.unique(df1.index.date):
        m = df1.index.date == d
        b = df1[m]
        sig = b[(b.index.hour == 9) & (b.index.minute == entry_min)]
        if sig.empty:
            continue
        sig5 = df5_map.get(d)
        dl = daily_map.get(d)
        if sig5 is None or dl is None or pd.isna(dl["gap"]):
            continue
        gap_ok = dl["gap"] < gap_thresh if gap_op == "<" else dl["gap"] > gap_thresh
        prev_ok = dl["prev_day_up"] == (not prev_red)
        if not gap_ok or not prev_ok:
            continue
        entry = float(sig["close"].iloc[0])
        rest = b[b.index > sig.index[0]]
        rest = rest[rest.index.hour * 60 + rest.index.minute <= exit_min]
        if sl_mult:
            stop_dist = float(a5.loc[sig5]) * sl_mult
            if side > 0:
                stop = entry - stop_dist
                hi = np.flatnonzero(rest["low"].values <= stop)
            else:
                stop = entry + stop_dist
                hi = np.flatnonzero(rest["high"].values >= stop)
            if len(hi):
                exit_p, reason = stop, "STOP"
            else:
                exit_p, reason = float(rest["close"].iloc[-1]), "TIME"
        else:
            exit_p, reason = float(rest["close"].iloc[-1]), "TIME"
        qty = int((INIT * RN) / entry)
        pnl = qty * side * (exit_p - entry) - 2 * FEES * qty * entry
        trades.append({"date": d, "pnl": pnl, "reason": reason, "entry": entry})
    t = pd.DataFrame(trades)
    if t.empty:
        return None
    w = t["pnl"] > 0
    gl = -t.loc[~w, "pnl"].sum()
    return {
        "n": len(t), "net": t["pnl"].sum(), "ret": t["pnl"].sum() / INIT * 100,
        "wr": w.mean() * 100, "pf": t.loc[w, "pnl"].sum() / gl if gl > 0 else np.inf,
        "worst": t["pnl"].min(), "t": t,
    }


def show(label, r):
    if r is None:
        print(f"  {label:36s} no trades")
        return r
    print(f"  {label:36s} n={r['n']:>3}  net={r['net']:>9,.0f} Rs  ret={r['ret']:>6.2f}%  "
          f"WR={r['wr']:>5.1f}%  PF={r['pf']:>5.2f}  worst={r['worst']:>9,.0f}")
    return r


print("=" * 104)
print("RELIANCE validation  (same 6-month window, 0.05%/side costs, 20% notional)")
print("=" * 104)

print("\nA) The exact SBIN config — short on gap < -0.05% after a RED day:")
show("short gap-down + red prev, no stop", day_trades("RELIANCE", 20, -1, "<", -0.05, True, 0))
show("short gap-down + red prev, 4xATR stop", day_trades("RELIANCE", 20, -1, "<", -0.05, True, 4.0))

print("\nB) RELIANCE mirror — short on gap > +0.05% after a GREEN day (fade):")
show("short gap-up + green prev, no stop", day_trades("RELIANCE", 20, -1, ">", 0.05, False, 0))
show("short gap-up + green prev, 4xATR stop", day_trades("RELIANCE", 20, -1, ">", 0.05, False, 4.0))

print("\nC) RELIANCE bounce — long on gap < -0.05% after a RED day:")
show("long gap-down + red prev, no stop", day_trades("RELIANCE", 20, 1, "<", -0.05, True, 0))
show("long gap-down + red prev, 4xATR stop", day_trades("RELIANCE", 20, 1, "<", -0.05, True, 4.0))

print("\nD) Entry-time robustness (best candidates, no stop):")
for label, kw in [
    ("short gap-up+green @09:15", dict(symbol="RELIANCE", entry_min=15, side=-1, gap_op=">", gap_thresh=0.05, prev_red=False, sl_mult=0)),
    ("short gap-up+green @09:20", dict(symbol="RELIANCE", entry_min=20, side=-1, gap_op=">", gap_thresh=0.05, prev_red=False, sl_mult=0)),
    ("short gap-up+green @09:25", dict(symbol="RELIANCE", entry_min=25, side=-1, gap_op=">", gap_thresh=0.05, prev_red=False, sl_mult=0)),
    ("short gap-up+green @09:30", dict(symbol="RELIANCE", entry_min=30, side=-1, gap_op=">", gap_thresh=0.05, prev_red=False, sl_mult=0)),
]:
    show(label, day_trades(**kw))

print("\nE) Half-sample split (short gap-up+green, no stop):")
t = day_trades("RELIANCE", 20, -1, ">", 0.05, False, 0)["t"].sort_values("date").reset_index(drop=True)
half = len(t) // 2
for label, sub in (("first half ", t.iloc[:half]), ("second half", t.iloc[half:])):
    print(f"  {label}  n={len(sub):>3}  sum={sub['pnl'].sum():>9,.0f} Rs  "
          f"WR={(sub['pnl']>0).mean()*100:>4.0f}%")
