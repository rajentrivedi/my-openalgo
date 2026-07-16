"""
61-EMA Intraday Strategy Backtest (RELIANCE + SBIN)
Event-driven reproduction of the OpenAlgo strategies in
strategies/reliance_intraday.py and strategies/SBIN_INTRADAY.py

Fidelity notes:
- Signals are evaluated on CLOSED 5-minute candles (strategy thread).
- Entries happen at the signal candle close (reference price).
- Stop / TP1 (50%) / TP2 (25%) exits are fired INTRABAR on live LTP in the
  real bot (WebSocket thread). We simulate that using the 1-minute bars we
  have in the Historify DB: within each 5m bucket, each 1m bar acts as an
  LTP probe (high/low), so stop and partial-target hits can happen mid-candle.
- After TP1 the stop moves to breakeven; after TP2 a 5-bar swing trail is
  applied at candle closes; an EMA-close exit is checked at each candle close.
- Hard square-off at 15:15 IST.
- Indicator math is imported verbatim from the strategy modules.

Usage:
    venv/bin/python backtesting/61ema/61ema_backtest.py --symbol reliance
    venv/bin/python backtesting/61ema/61ema_backtest.py --symbol sbin
    venv/bin/python backtesting/61ema/61ema_backtest.py            # both
"""

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "db" / "historify.duckdb"
STRATEGY_FILES = {
    "reliance": ROOT / "strategies" / "reliance_intraday.py",
    "sbin": ROOT / "strategies" / "SBIN_INTRADAY.py",
}

INIT_CASH = 1_000_000.0
FEES = 0.0005        # proportional per order (0.05% round-trip approx)
FIXED_FEES = 20.0    # flat brokerage per order
WARMUP_BARS = 80     # skip the first N 5m candles (indicator warm-up)


def load_strategy(symbol: str):
    spec = importlib.util.spec_from_file_location(f"strat_{symbol}", STRATEGY_FILES[symbol])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_1m(symbol: str) -> pd.DataFrame:
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.execute(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM market_data WHERE symbol = ? AND exchange = 'NSE' AND interval = '1m' "
        "ORDER BY timestamp",
        [symbol.upper()],
    ).fetchdf()
    con.close()
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("datetime").drop(columns=["timestamp"])
    df.index = df.index.tz_convert("Asia/Kolkata")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c])
    return df.sort_index()


def resample_5m(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.resample("5min", origin="start_day", offset="9h15min", label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def vectorized_signal(ind: pd.DataFrame, mod) -> tuple[pd.Series, pd.Series]:
    """Exact vectorized port of mod.evaluate_signal (row i == last closed candle)."""
    c = ind["close"]
    e = ind["ema"]
    dist_pct = (c - e).abs() / e * 100.0

    macd, sig, hist = ind["macd"], ind["macd_sig"], ind["hist"]
    cross_up = (macd > sig) & (macd.shift(1) <= sig.shift(1))
    cross_dn = (macd < sig) & (macd.shift(1) >= sig.shift(1))
    macd_bull = cross_up & (macd > 0) & (sig > 0)
    macd_bear = cross_dn & (macd < 0) & (sig < 0)

    hist_prev = hist.shift(mod.MACD_MOMENTUM_BARS)
    l_mom = (not mod.USE_MACD_MOMENTUM_FILTER) | (hist > hist_prev)
    s_mom = (not mod.USE_MACD_MOMENTUM_FILTER) | (hist < hist_prev)

    ema_prev = e.shift(mod.EMA_SLOPE_BARS)
    l_slope = (not mod.USE_EMA_SLOPE_FILTER) | (e > ema_prev)
    s_slope = (not mod.USE_EMA_SLOPE_FILTER) | (e < ema_prev)

    l_vwap = (not mod.USE_VWAP_FILTER) | (c > ind["vwap"])
    s_vwap = (not mod.USE_VWAP_FILTER) | (c < ind["vwap"])

    adx_ok = ind["adx"] >= mod.MIN_ADX
    l_adx = (not mod.USE_ADX_FILTER) | (adx_ok & ((not mod.USE_DI_FILTER) | (ind["pdi"] > ind["mdi"])))
    s_adx = (not mod.USE_ADX_FILTER) | (adx_ok & ((not mod.USE_DI_FILTER) | (ind["mdi"] > ind["pdi"])))

    vol_ok = (not mod.USE_VOLUME_FILTER) | (ind["volume"] >= ind["vol_sma"] * mod.VOLUME_MULTIPLIER)

    dist_ok = ((not mod.USE_MIN_EMA_DIST_FILTER) | (dist_pct >= mod.MIN_EMA_DIST_PCT)) & (
        (not mod.USE_MAX_EMA_DIST_FILTER) | (dist_pct <= mod.MAX_EMA_DIST_PCT)
    )

    ema_reg_prev = e.shift(mod.REGIME_SLOPE_BARS)
    slope_pct = (e - ema_reg_prev) / ema_reg_prev * 100.0
    bull_regime = (c > e) & (ind["adx"] >= mod.REGIME_ADX_THRESHOLD) & (ind["pdi"] > ind["mdi"]) & (slope_pct >= mod.REGIME_MIN_SLOPE_PCT)
    bear_regime = (c < e) & (ind["adx"] >= mod.REGIME_ADX_THRESHOLD) & (ind["mdi"] > ind["pdi"]) & (slope_pct <= -mod.REGIME_MIN_SLOPE_PCT)
    l_reg = (not mod.USE_REGIME_FILTER) | bull_regime
    s_reg = (not mod.USE_REGIME_FILTER) | bear_regime

    long_sig = (
        mod.ENABLE_LONG
        & (c > e)
        & (ind["rsi"] > mod.RSI_BUY_LEVEL)
        & macd_bull & l_mom & l_slope & l_vwap & l_adx & vol_ok & dist_ok & l_reg
    )
    short_sig = (
        mod.ENABLE_SHORT
        & (c < e)
        & (ind["rsi"] < mod.RSI_SELL_LEVEL)
        & macd_bear & s_mom & s_slope & s_vwap & s_adx & vol_ok & dist_ok & s_reg
    )
    return long_sig.fillna(False), short_sig.fillna(False)


def compute_risk(mod, row, direction: str) -> float:
    c = float(row["close"])
    if getattr(mod, "STOP_MODE", "ATR") == "SWING":
        risk = c - float(row["swing_low"]) if direction == "LONG" else float(row["swing_high"]) - c
    else:
        risk = float(row["atr"]) * mod.ATR_STOP_MULTIPLIER
    return risk


class Position:
    __slots__ = ("side", "qty_remaining", "entry", "risk", "stop", "tp1", "tp2",
                 "tp1_done", "tp2_done", "entry_ts", "legs")

    def __init__(self, side, qty, entry, risk, stop, tp1, tp2, ts):
        self.side = side
        self.qty_remaining = qty
        self.entry = entry
        self.risk = risk
        self.stop = stop
        self.tp1 = tp1
        self.tp2 = tp2
        self.tp1_done = False
        self.tp2_done = False
        self.entry_ts = ts
        self.legs = []

    def pnl(self):
        return sum(leg[3] for leg in self.legs)


def run_symbol(symbol: str, mod):
    df1 = load_1m(symbol)
    df5 = resample_5m(df1)
    ind = mod.compute_indicators(df5)
    long_sig, short_sig = vectorized_signal(ind, mod)

    # Map each 1m bar to its right-labelled 5m bucket (same grid the resample uses).
    bucket_labels = df1.index.ceil("5min")
    bars = df1.reset_index()
    bar_lookup = {row["datetime"]: row for _, row in bars.iterrows()}
    from collections import defaultdict
    buckets = defaultdict(list)
    for ts in df1.index:
        lbl = ts.ceil("5min")
        if lbl in df5.index:
            buckets[lbl].append(ts)

    pos = None
    trades = []
    daily_pnl = {}

    def record_leg(qty, price, reason, ts, fee=True):
        if qty <= 0:
            return
        if pos.side == "LONG":
            pnl = qty * (price - pos.entry)
        else:
            pnl = qty * (pos.entry - price)
        fees = (FIXED_FEES + FEES * qty * price) if fee else 0.0
        net = pnl - fees
        pos.legs.append((ts, reason, qty, net, price))
        pos.qty_remaining -= qty

    def close_trade(ts):
        nonlocal trades, pos
        if pos is None:
            return
        entry_qty = mod.QUANTITY
        trades.append({
            "entry_ts": pos.entry_ts,
            "exit_ts": ts,
            "side": pos.side,
            "qty": entry_qty,
            "entry": pos.entry,
            "risk": pos.risk,
            "pnl": pos.pnl(),
            "r_multiple": pos.pnl() / (entry_qty * pos.risk) if pos.risk > 0 else 0.0,
            "legs": len(pos.legs),
            "reasons": ",".join(sorted(set(l[1] for l in pos.legs))),
        })
        day = pos.entry_ts.date()
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pos.pnl()
        pos = None

    # Main event loop over 1m bars, grouped into 5m buckets.
    n = len(df5)
    for i in range(n):
        T = df5.index[i]
        row = ind.iloc[i]
        hm = (T.hour, T.minute)

        bts = buckets.get(T, [])
        bucket_bars = [bar_lookup[ts] for ts in bts]

        if pos is not None:
            # Intrabar LTP probes (WebSocket exits).
            for b in bucket_bars:
                if pos is None:
                    break
                o, h, l, c, ts = b["open"], b["high"], b["low"], b["close"], b["datetime"]
                if pos.side == "LONG":
                    if l <= pos.stop:
                        qty = pos.qty_remaining
                        record_leg(qty, pos.stop, "STOP-LOSS", ts)
                        close_trade(ts)
                        continue
                    if not pos.tp1_done and h >= pos.tp1:
                        qty = int(mod.QUANTITY * mod.FIRST_EXIT_PCT)
                        qty = min(qty, pos.qty_remaining)
                        record_leg(qty, pos.tp1, "TP1", ts)
                        pos.tp1_done = True
                        if mod.MOVE_TO_BREAKEVEN_AT_TP1 and pos.qty_remaining > 0:
                            pos.stop = max(pos.stop, pos.entry)
                    if not pos.tp2_done and h >= pos.tp2:
                        qty = int(mod.QUANTITY * mod.SECOND_EXIT_PCT)
                        qty = min(qty, pos.qty_remaining)
                        record_leg(qty, pos.tp2, "TP2", ts)
                        pos.tp2_done = True
                    # After a breakeven move, the same bar's low may now be a stop.
                    if pos.qty_remaining > 0 and l <= pos.stop:
                        qty = pos.qty_remaining
                        record_leg(qty, pos.stop, "STOP-LOSS", ts)
                        close_trade(ts)
                else:  # SHORT
                    if h >= pos.stop:
                        qty = pos.qty_remaining
                        record_leg(qty, pos.stop, "STOP-LOSS", ts)
                        close_trade(ts)
                        continue
                    if not pos.tp1_done and l <= pos.tp1:
                        qty = int(mod.QUANTITY * mod.FIRST_EXIT_PCT)
                        qty = min(qty, pos.qty_remaining)
                        record_leg(qty, pos.tp1, "TP1", ts)
                        pos.tp1_done = True
                        if mod.MOVE_TO_BREAKEVEN_AT_TP1 and pos.qty_remaining > 0:
                            pos.stop = min(pos.stop, pos.entry)
                    if not pos.tp2_done and l <= pos.tp2:
                        qty = int(mod.QUANTITY * mod.SECOND_EXIT_PCT)
                        qty = min(qty, pos.qty_remaining)
                        record_leg(qty, pos.tp2, "TP2", ts)
                        pos.tp2_done = True
                    if pos.qty_remaining > 0 and h >= pos.stop:
                        qty = pos.qty_remaining
                        record_leg(qty, pos.stop, "STOP-LOSS", ts)
                        close_trade(ts)
            if pos is not None and pos.qty_remaining == 0:
                close_trade(T)

        # Hard square-off at 15:15 -- closes anything still open.
        if pos is not None and hm >= mod.SQUARE_OFF:
            record_leg(pos.qty_remaining, float(row["close"]), "EOD-SQUARE", T)
            close_trade(T)

        if pos is not None:
            # Candle-close management (strategy thread).
            if pos.tp2_done and mod.TRAIL_AFTER_TP2 and pos.qty_remaining > 0:
                if pos.side == "LONG":
                    pos.stop = max(pos.stop, float(row["trail_low"]))
                else:
                    pos.stop = min(pos.stop, float(row["trail_high"]))
            if mod.USE_EMA_EXIT and pos.qty_remaining > 0:
                c = float(row["close"])
                e = float(row["ema"])
                if (pos.side == "LONG" and c < e) or (pos.side == "SHORT" and c > e):
                    record_leg(pos.qty_remaining, c, "EMA-EXIT", T)
                    close_trade(T)
            if pos is not None and pos.qty_remaining == 0:
                close_trade(T)

        # Entry evaluation (only when flat).
        hm = (T.hour, T.minute)
        if pos is None and i >= WARMUP_BARS and mod.ENTRY_START <= hm < mod.ENTRY_END:
            side = None
            if long_sig.iloc[i]:
                side = "LONG"
            elif short_sig.iloc[i]:
                side = "SHORT"
            if side:
                c = float(row["close"])
                risk = compute_risk(mod, row, side)
                if risk > 0 and risk / c * 100.0 <= mod.MAX_RISK_PCT:
                    if side == "LONG":
                        stop, tp1, tp2 = c - risk, c + risk * mod.FIRST_TARGET_R, c + risk * mod.SECOND_TARGET_R
                    else:
                        stop, tp1, tp2 = c + risk, c - risk * mod.FIRST_TARGET_R, c - risk * mod.SECOND_TARGET_R
                    pos = Position(side, mod.QUANTITY, c, risk, stop, tp1, tp2, T)
                    pos.legs.append((T, "ENTRY", mod.QUANTITY, -(FIXED_FEES + FEES * mod.QUANTITY * c), c))

    # Any position still open at the last bar -> square off at last close.
    if pos is not None:
        last_close = float(df5["close"].iloc[-1])
        record_leg(pos.qty_remaining, last_close, "EOD-SQUARE", df5.index[-1])
        close_trade(df5.index[-1])

    trades_df = pd.DataFrame(trades)
    return trades_df, daily_pnl, ind


def analyze(symbol, mod, trades_df, daily_pnl, all_dates):
    print("\n" + "=" * 70)
    print(f" {symbol} | {getattr(mod, 'STOP_MODE', 'ATR')} stop | 61-EMA intraday")
    print(f" Trades: {len(trades_df)} | Period trades: {trades_df['entry_ts'].min()} .. {trades_df['entry_ts'].max()}")
    print("=" * 70)
    if trades_df.empty:
        print(" No trades generated.")
        return None

    wins = trades_df["pnl"] > 0
    gross_win = trades_df.loc[wins, "pnl"].sum()
    gross_loss = -trades_df.loc[~wins, "pnl"].sum()
    net = trades_df["pnl"].sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    days_raw = pd.Series(daily_pnl).sort_index()
    days_raw.index = pd.DatetimeIndex(days_raw.index)
    trade_days = pd.DatetimeIndex(all_dates)
    all_days = trade_days.union(days_raw.index).drop_duplicates().sort_values()
    days = days_raw.reindex(all_days).fillna(0.0)
    equity = INIT_CASH + days.cumsum()
    daily_ret = equity.pct_change().fillna(0.0)
    total_return = equity.iloc[-1] / INIT_CASH - 1.0
    max_dd = (equity / equity.cummax() - 1.0).min()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0.0
    downside = daily_ret[daily_ret < 0].std()
    sortino = daily_ret.mean() / downside * np.sqrt(252) if downside > 0 else 0.0
    avg_r = trades_df["r_multiple"].mean()

    print(f" Net P&L        : {net:,.0f} Rs  ({total_return * 100:.2f}% on {INIT_CASH:,.0f} capital)")
    print(f" Gross win/loss : {gross_win:,.0f} / {gross_loss:,.0f}  |  Profit factor {pf:.2f}")
    print(f" Win rate       : {wins.mean() * 100:.1f}%  ({wins.sum()}/{len(trades_df)})")
    print(f" Avg R-multiple : {avg_r:.2f}  |  Best {trades_df['r_multiple'].max():.2f}R / Worst {trades_df['r_multiple'].min():.2f}R")
    print(f" Max drawdown   : {max_dd * 100:.2f}%  |  Sharpe {sharpe:.2f} / Sortino {sortino:.2f}")
    print(f" Avg legs/trade : {trades_df['legs'].mean():.1f}  |  Exit mix: {trades_df['reasons'].value_counts().to_dict()}")

    # Buy & hold the same symbol over the same period.
    b = load_1m(symbol)
    first_open = float(b["open"].iloc[0])
    last_close = float(b["close"].iloc[-1])
    bh = last_close / first_open - 1.0
    print(f" Buy & hold      : {bh * 100:+.2f}%  (strategy {total_return * 100:+.2f}%)")

    return {"equity": equity, "daily_ret": daily_ret, "trades_df": trades_df, "net": net,
            "pf": pf, "win_rate": wins.mean(), "total_return": total_return, "max_dd": max_dd,
            "bh": bh, "sharpe": sharpe, "sortino": sortino}


def save_outputs(symbol, res, mod):
    out = ROOT / "backtesting" / "61ema"
    out.mkdir(parents=True, exist_ok=True)

    trades_df = res["trades_df"].copy()
    trades_df["pnl"] = trades_df["pnl"].round(2)
    trades_df.to_csv(out / f"{symbol}_61ema_trades.csv", index=False)

    label = f"{symbol} 61-EMA {'SWING' if getattr(mod, 'STOP_MODE', 'ATR') == 'SWING' else 'ATR'} - Intraday"
    strategy_returns = res["daily_ret"].copy()
    strategy_returns.name = f"{label}"

    fig = None
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        equity = res["equity"]
        drawdown = equity / equity.cummax() - 1.0
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.7, 0.3], vertical_spacing=0.05)
        fig.add_trace(go.Scatter(x=equity.index, y=equity.values, name="Equity",
                                 line=dict(color="#00d4ff")), row=1, col=1)
        fig.add_trace(go.Scatter(x=drawdown.index, y=drawdown.values * 100, name="Drawdown",
                                 line=dict(color="#ff5252")), row=2, col=1)
        fig.update_layout(template="plotly_dark", title=f"{label}",
                          height=650, showlegend=True)
        fig.update_yaxes(title_text="Equity (Rs)", row=1, col=1)
        fig.update_yaxes(title_text="DD %", row=2, col=1)
        fig.write_html(out / f"{symbol}_61ema.html")
        print(f" Chart saved: {out / f'{symbol}_61ema.html'}")
    except Exception as e:
        print(f" Plotly chart skipped: {e}")

    # OpenStatz tearsheet (self-contained HTML).
    try:
        import openstatz as ostz
        ostz.dashboard(strategy_returns, output=str(out / f"{symbol}_61ema_tearsheet.html"),
                       title=f"{label}", open_browser=False)
        print(f" Tearsheet saved: {out / f'{symbol}_61ema_tearsheet.html'}")
    except Exception as e:
        print(f" OpenStatz tearsheet skipped: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=["reliance", "sbin"], default=None)
    args = ap.parse_args()

    symbols = [args.symbol] if args.symbol else ["reliance", "sbin"]
    for sym in symbols:
        mod = load_strategy(sym)
        trades_df, daily_pnl, ind = run_symbol(sym, mod)
        all_dates = pd.DatetimeIndex(ind.index.date)
        res = analyze(sym, mod, trades_df, daily_pnl, all_dates)
        if res:
            save_outputs(sym, res, mod)


if __name__ == "__main__":
    main()
