"""
Day-bias intraday short strategy exploration on SBIN (Feb-Aug 2026).

Hypothesis from day-level stats: SBI drifted down (bear regime), and gap-down
days keep drifting lower (mean open->close -0.24%). Test short-at-open on
gap-down days with optional filters (prev-day continuation, VWAP) and ATR stop.

Signals on the 09:20 5m close, entry at that close, SL probed intrabar on 1m,
hard cover at 15:15. Costs 0.05%/side.
"""

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backtesting" / "sbi_pine6"))
from backtest import load_1m, resample_5m, atr, vwap_day  # noqa: E402

INIT_CASH = 1_000_000.0
RISK_NOTIONAL = 0.20
FEES = 0.0005


def run_day_bias(df1: pd.DataFrame, df5: pd.DataFrame, gap_min: float, sl_mult: float,
                 prev_down: bool, vwap_filter: bool, use_sl: bool) -> tuple[list, pd.Series]:
    day = df1.index.date
    daily = df1.resample("1D").agg({"open": "first", "close": "last"}).dropna()
    daily["prev_close"] = daily["close"].shift(1)
    daily["prev_day_up"] = daily["close"].shift(1) > daily["open"].shift(1)
    daily["gap"] = (daily["open"] / daily["prev_close"] - 1) * 100

    a5 = atr(df5["high"], df5["low"], df5["close"], 14)
    vwap = vwap_day(df5["high"], df5["low"], df5["close"], df5["volume"])

    trades = []
    daily_pnl = {}
    daily_map = {ts.date(): row for ts, row in daily.iterrows()}
    df5_map = {ts.normalize().date(): ts for ts in df5.index}
    for d in pd.unique(day):
        m = df1.index.date == d
        b = df1[m]
        # signal bar = 09:20 5m close
        sig = b[(b.index.hour == 9) & (b.index.minute == 20)]
        if sig.empty:
            continue
        sig5_ts = df5_map.get(d)
        if sig5_ts is None:
            continue
        dl = daily_map.get(d)
        if dl is None:
            continue
        gap = dl["gap"]
        if np.isnan(gap) or gap >= gap_min:
            continue
        if prev_down and dl["prev_day_up"]:
            continue
        entry = float(sig["close"].iloc[0])
        if vwap_filter and entry >= float(vwap.loc[sig5_ts]):
            continue
        risk = float(a5.loc[sig5_ts]) * sl_mult if use_sl else np.inf
        stop = entry + risk if use_sl else np.inf
        # probe rest of day on 1m (vectorized)
        rest = b[b.index > sig.index[0]]
        rest = rest[rest.index.hour * 60 + rest.index.minute <= 15 * 60 + 15]
        hit = None
        if use_sl:
            idx = np.flatnonzero(rest["high"].values >= stop)
            if len(idx):
                hit = idx[0]
        if hit is not None:
            exit_ts = rest.index[hit]
            exit_price = stop
            reason = "STOP"
        else:
            exit_ts = rest.index[-1]
            exit_price = float(rest["close"].iloc[-1])
            reason = "EOD"
        qty = int((INIT_CASH * RISK_NOTIONAL) / entry)
        pnl = qty * (entry - exit_price) - 2 * FEES * qty * entry
        trades.append({"date": d, "entry": entry, "exit": exit_price, "reason": reason,
                       "pnl": pnl, "r": pnl / (qty * risk) if use_sl and risk > 0 else np.nan})
        daily_pnl[d] = pnl
    return trades, pd.Series(daily_pnl)


def main():
    df1 = load_1m("SBIN")
    df5 = resample_5m(df1)
    print(f"Bars: {len(df5)} 5m, {len(df1)} 1m\n")

    results = []
    for gap_min, sl_mult, prev_down, vwap_f, use_sl in itertools.product(
        (-0.05, -0.1, -0.2), (1.0, 1.5, 2.0), (False, True), (False, True), (True, False)
    ):
        trades, daily = run_day_bias(df1, df5, gap_min, sl_mult, prev_down, vwap_f, use_sl)
        if not trades:
            continue
        t = pd.DataFrame(trades)
        net = t["pnl"].sum()
        results.append({
            "gap": gap_min, "sl": sl_mult, "prev": int(prev_down), "vwap": int(vwap_f),
            "n": len(t), "net": net, "ret": net / INIT_CASH * 100,
            "wr": (t["pnl"] > 0).mean() * 100, "pf": None,
        })
        wins = t.loc[t["pnl"] > 0, "pnl"].sum()
        losses = -t.loc[t["pnl"] <= 0, "pnl"].sum()
        results[-1]["pf"] = wins / losses if losses > 0 else np.inf

    results.sort(key=lambda r: r["net"], reverse=True)
    print("gap   sl  prev vwap   n   net(Rs)    ret%    WR%     PF")
    for r in results[:20]:
        print(f"{r['gap']:>5} {r['sl']:>4} {r['prev']:>4} {r['vwap']:>4} "
              f"{r['n']:>4} {r['net']:>10,.0f} {r['ret']:>7.2f} {r['wr']:>6.1f} {r['pf']:>6.2f}")


if __name__ == "__main__":
    main()
