"""Final run of the chosen SBIN gap-continuation short config -> trades CSV + chart."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT))
from backtest import atr, load_1m, resample_5m, vwap_day  # noqa: E402

INIT_CASH = 1_000_000.0
RISK_NOTIONAL = 0.20
FEES = 0.0005          # per side
GAP_MIN = -0.05
SL_ATR = 4.0
ENTRY_H, ENTRY_M = 9, 20
EXIT_H, EXIT_M = 15, 15


def main():
    df1 = load_1m("SBIN")
    df5 = resample_5m(df1)
    a5 = atr(df5["high"], df5["low"], df5["close"], 14)

    daily = df1.resample("1D").agg({"open": "first", "close": "last"}).dropna()
    daily["prev_close"] = daily["close"].shift(1)
    daily["prev_day_up"] = daily["close"].shift(1) > daily["open"].shift(1)
    daily["gap"] = (daily["open"] / daily["prev_close"] - 1) * 100

    daily_map = {ts.date(): row for ts, row in daily.iterrows()}
    df5_map = {ts.normalize().date(): ts for ts in df5.index}

    trades = []
    daily_pnl = {}
    for d in pd.unique(df1.index.date):
        m = df1.index.date == d
        b = df1[m]
        sig = b[(b.index.hour == ENTRY_H) & (b.index.minute == ENTRY_M)]
        if sig.empty:
            continue
        sig5 = df5_map.get(d)
        dl = daily_map.get(d)
        if sig5 is None or dl is None or pd.isna(dl["gap"]):
            continue
        if dl["gap"] >= GAP_MIN or dl["prev_day_up"]:
            continue
        entry = float(sig["close"].iloc[0])
        risk = float(a5.loc[sig5]) * SL_ATR
        stop = entry + risk
        rest = b[b.index > sig.index[0]]
        rest = rest[rest.index.hour * 60 + rest.index.minute <= EXIT_H * 60 + EXIT_M]
        hit = np.flatnonzero(rest["high"].values >= stop)
        if len(hit):
            exit_ts, exit_p, reason = rest.index[hit[0]], stop, "STOP"
        else:
            exit_ts, exit_p, reason = rest.index[-1], float(rest["close"].iloc[-1]), "EOD"
        qty = int((INIT_CASH * RISK_NOTIONAL) / entry)
        pnl = qty * (entry - exit_p) - 2 * FEES * qty * entry
        trades.append({"date": d, "entry_ts": sig.index[0], "exit_ts": exit_ts,
                       "entry": entry, "exit": exit_p, "reason": reason,
                       "qty": qty, "pnl": pnl})
        daily_pnl[d] = pnl

    t = pd.DataFrame(trades).sort_values("date").reset_index(drop=True)
    t.to_csv(OUT / "sbin_gap_continuation_trades.csv", index=False)

    days = pd.Series(daily_pnl).sort_index()
    days.index = pd.DatetimeIndex(days.index)
    all_days = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(df1.index.date).normalize()) | set(days.index)))
    days = days.reindex(all_days).fillna(0.0)
    equity = INIT_CASH + days.cumsum()
    dd = equity / equity.cummax() - 1.0

    wins = t["pnl"] > 0
    gw, gl = t.loc[wins, "pnl"].sum(), -t.loc[~wins, "pnl"].sum()
    print(f"Trades : {len(t)}  ({t['date'].min()} .. {t['date'].max()})")
    print(f"Net P&L: {t['pnl'].sum():,.0f} Rs  ({t['pnl'].sum()/INIT_CASH*100:.2f}% on {INIT_CASH:,.0f})")
    print(f"WinRate: {wins.mean()*100:.1f}%   ProfitFactor: {gw/gl:.2f}")
    print(f"Max DD : {dd.min()*100:.2f}%   Avg trade: {t['pnl'].mean():,.0f} Rs")
    print(f"Exits  : {t['reason'].value_counts().to_dict()}")

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                            vertical_spacing=0.05)
        fig.add_trace(go.Scatter(x=equity.index, y=equity.values, name="Equity",
                                 line=dict(color="#00d4ff")), row=1, col=1)
        fig.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, name="Drawdown",
                                 line=dict(color="#ff5252")), row=2, col=1)
        fig.update_layout(template="plotly_dark",
                          title="SBIN Gap-Continuation Short - Equity & Drawdown", height=600)
        fig.write_html(OUT / "sbin_gap_continuation.html")
        print(f"Chart saved: {OUT / 'sbin_gap_continuation.html'}")
    except Exception as e:
        print(f"Chart skipped: {e}")


if __name__ == "__main__":
    main()
