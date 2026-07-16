"""
SBI Intraday Pine v6 Candidate Backtest
=======================================
Faithful reproduction of how a TradingView Pine v6 strategy executes, so the
winning config can be ported 1:1 to Pine:

- Signals are evaluated on CLOSED 5-minute candles (strategy on chart).
- Entry fires at the signal candle close (process_orders_on_close = true).
- Stop / target exits are probed INTRABAR using the 1-minute bars inside each
  5m bucket (high/low act as LTP probes), like a live feed hitting the stop.
- Hard square-off at 15:15 IST.
- One trade per day max.

Indicators match Pine v6 math exactly:
  * EMA  -> pandas ewm(span=n)            (same alpha = 2/(n+1))
  * ATR  -> Wilder RMA of true range      (same as ta.atr)
  * RSI  -> Wilder RMA-based               (same as ta.rsi)
  * ADX  -> Wilder DMI                      (same as ta.dmi)
  * VWAP -> per-day cumulative tp*vol/vol   (same as ta.vwap, session-anchored)

Costs (realistic NSE intraday, discount broker):
  * Per-side charges ~0.05% round trip ~0.10% (brokerage 0.03% + STT + charges).

Usage:
    uv run python backtesting/sbi_pine6/backtest.py            # parameter scan
    uv run python backtesting/sbi_pine6/backtest.py --only best # just chosen config
"""

import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "db" / "historify.duckdb"
OUT = Path(__file__).resolve().parent

INIT_CASH = 1_000_000.0
RISK_NOTIONAL = 0.20          # 20% of capital per trade
FEES = 0.0005                 # per side (~0.05%)
WARMUP_BARS = 100             # skip indicator warm-up
SQ_HOUR, SQ_MIN = 15, 15      # square-off time


# ---------------------------------------------------------------- data ----

def load_1m(symbol: str = "SBIN") -> pd.DataFrame:
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


def resample_1d(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


# ------------------------------------------------------ pine indicators ----

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def wilder_rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return wilder_rma(tr, n)


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = wilder_rma(d.clip(lower=0.0), n)
    dn = wilder_rma(-d.clip(upper=0.0), n)
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def dmi(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14):
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr_ = wilder_rma(tr, n)
    pdi = 100 * wilder_rma(plus_dm, n) / atr_
    mdi = 100 * wilder_rma(minus_dm, n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = wilder_rma(dx.fillna(0.0), n)
    return pdi, mdi, adx


def vwap_day(h: pd.Series, l: pd.Series, c: pd.Series, v: pd.Series) -> pd.Series:
    tp = (h + l + c) / 3
    tpv = tp * v
    day = h.index.date
    out = pd.Series(np.nan, index=h.index)
    for d in pd.unique(day):
        m = h.index.date == d
        out[m] = tpv[m].cumsum() / v[m].cumsum().replace(0, np.nan)
    return out.ffill()


# ------------------------------------------------------------ backtest ----

class Position:
    __slots__ = ("side", "qty", "entry", "risk", "stop", "tp", "be_done", "entry_ts", "legs")

    def __init__(self, side, qty, entry, risk, stop, tp, ts):
        self.side = side
        self.qty = qty
        self.entry = entry
        self.risk = risk
        self.stop = stop
        self.tp = tp
        self.be_done = False
        self.entry_ts = ts
        self.legs = []  # (ts, reason, qty, net_pnl, price)

    def pnl(self):
        return sum(leg[3] for leg in self.legs)


def run_strategy(cfg: dict, df5: pd.DataFrame, ind: pd.DataFrame, bars1: pd.DataFrame,
                 buckets: dict) -> tuple[pd.DataFrame, pd.Series]:
    """Event-driven run. Returns (trades_df, daily_pnl)."""
    long_sig, short_sig = build_signals(cfg, ind)

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
        fees = (FEES * qty * price) if fee else 0.0
        pos.legs.append((ts, reason, qty, pnl - fees, price))
        pos.qty -= qty

    def close_trade(ts):
        nonlocal trades, pos
        if pos is None:
            return
        entry_qty = sum(1 for _ in [0])  # placeholder
        trades.append({
            "entry_ts": pos.entry_ts,
            "exit_ts": ts,
            "side": pos.side,
            "qty": pos.qty + sum(l[2] for l in pos.legs),
            "entry": pos.entry,
            "risk": pos.risk,
            "pnl": pos.pnl(),
            "r_multiple": pos.pnl() / (entry_qty * pos.risk) if pos.risk > 0 else 0.0,
            "reasons": ",".join(sorted(set(l[1] for l in pos.legs))),
        })
        day = pos.entry_ts.date()
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pos.pnl()
        pos = None

    n = len(df5)
    for i in range(n):
        T = df5.index[i]
        row = ind.iloc[i]
        hm = (T.hour, T.minute)
        bts = buckets.get(T, [])
        bucket_bars = [bars1.loc[ts] for ts in bts]

        # --- manage open position (intrabar LTP probes) ---
        if pos is not None:
            for b in bucket_bars:
                if pos is None:
                    break
                o, h, l, c, ts = b["open"], b["high"], b["low"], b["close"], b.name
                if pos.side == "LONG":
                    if l <= pos.stop:
                        record_leg(pos.qty, pos.stop, "STOP", ts)
                        close_trade(ts)
                        continue
                    if not pos.be_done and h >= pos.tp:
                        # partial at target -> move stop to breakeven
                        qty = int(pos.qty * 0.5)
                        qty = max(qty, 1)
                        qty = min(qty, pos.qty)
                        record_leg(qty, pos.tp, "TP1", ts)
                        pos.be_done = True
                        if pos.qty > 0:
                            pos.stop = max(pos.stop, pos.entry)
                        if pos.qty > 0 and l <= pos.stop:
                            record_leg(pos.qty, pos.stop, "STOP-BE", ts)
                            close_trade(ts)
                else:
                    if h >= pos.stop:
                        record_leg(pos.qty, pos.stop, "STOP", ts)
                        close_trade(ts)
                        continue
                    if not pos.be_done and l <= pos.tp:
                        qty = int(pos.qty * 0.5)
                        qty = max(qty, 1)
                        qty = min(qty, pos.qty)
                        record_leg(qty, pos.tp, "TP1", ts)
                        pos.be_done = True
                        if pos.qty > 0:
                            pos.stop = min(pos.stop, pos.entry)
                        if pos.qty > 0 and h >= pos.stop:
                            record_leg(pos.qty, pos.stop, "STOP-BE", ts)
                            close_trade(ts)
            if pos is not None and pos.qty == 0:
                close_trade(T)

        # --- square off ---
        if pos is not None and hm >= (SQ_HOUR, SQ_MIN):
            record_leg(pos.qty, float(row["close"]), "EOD", T)
            close_trade(T)

        # --- entry (only when flat) ---
        if pos is None and i >= WARMUP_BARS and cfg["entry_start"] <= hm < cfg["entry_end"]:
            side = None
            if long_sig.iloc[i]:
                side = "LONG"
            elif short_sig.iloc[i]:
                side = "SHORT"
            if side and cfg["direction"] == "long":
                side = "LONG"
            if side:
                c = float(row["close"])
                risk = float(row["atr"]) * cfg["sl_atr"]
                if risk > 0:
                    qty = int((INIT_CASH * RISK_NOTIONAL) / c)
                    qty = max(qty, 1)
                    if side == "LONG":
                        stop, tp = c - risk, c + risk * cfg["rr"]
                    else:
                        stop, tp = c + risk, c - risk * cfg["rr"]
                    pos = Position(side, qty, c, risk, stop, tp, T)
                    pos.legs.append((T, "ENTRY", qty, -(FEES * qty * c), c))

    if pos is not None:
        last_close = float(df5["close"].iloc[-1])
        record_leg(pos.qty, last_close, "EOD", df5.index[-1])
        close_trade(df5.index[-1])

    # fix r_multiple using real entry qty
    for t in trades:
        t["r_multiple"] = t["pnl"] / (t["qty"] * t["risk"]) if t["risk"] > 0 else 0.0
    trades_df = pd.DataFrame(trades)
    daily = pd.Series(daily_pnl)
    return trades_df, daily


def build_signals(cfg: dict, ind: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    c, h, l = ind["close"], ind["high"], ind["low"]
    ema20, ema50 = ind["ema20"], ind["ema50"]
    vw, atr_ = ind["vwap"], ind["atr"]
    pdi, mdi, adx = ind["pdi"], ind["mdi"], ind["adx"]
    rs = ind["rsi"]
    vol, vsma = ind["volume"], ind["vol_sma"]
    orh, orl = ind["or_high"], ind["or_low"]
    d20 = ind["daily_ema20"]

    ema_bull = (c > ema20) & (ema20 > ema50)
    ema_bear = (c < ema20) & (ema20 < ema50)
    vw_bull = c > vw
    vw_bear = c < vw
    adx_ok = adx >= cfg["min_adx"]
    vol_ok = vol >= vsma * cfg["vol_mult"]
    daily_ok = cfg["use_daily"] and c > d20

    long_sig = (
        ema_bull & vw_bull & adx_ok & vol_ok & (rs > cfg["rsi_buy"])
        & (c > orh) & ind["post_or"]
        & (cfg["direction"] in ("both", "long"))
    )
    short_sig = (
        ema_bear & vw_bear & adx_ok & vol_ok & (rs < cfg["rsi_sell"])
        & (c < orl) & ind["post_or"]
        & (cfg["direction"] in ("both", "short"))
    )
    if cfg["use_daily"]:
        long_sig = long_sig & daily_ok
    return long_sig.fillna(False), short_sig.fillna(False)


# ------------------------------------------------------------- analysis ----

def analyze(cfg, trades_df, daily_pnl, all_dates):
    if trades_df.empty:
        return None
    wins = trades_df["pnl"] > 0
    gross_win = trades_df.loc[wins, "pnl"].sum()
    gross_loss = -trades_df.loc[~wins, "pnl"].sum()
    net = trades_df["pnl"].sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    days_raw = daily_pnl.sort_index()
    days_raw.index = pd.DatetimeIndex(days_raw.index)
    all_days = pd.DatetimeIndex(all_dates).union(days_raw.index).drop_duplicates().sort_values()
    days = days_raw.reindex(all_days).fillna(0.0)
    equity = INIT_CASH + days.cumsum()
    total_return = equity.iloc[-1] / INIT_CASH - 1.0
    max_dd = (equity / equity.cummax() - 1.0).min()
    daily_ret = equity.pct_change().fillna(0.0)
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0.0
    return {
        "cfg": cfg, "trades": len(trades_df), "net": net, "pf": pf,
        "win_rate": wins.mean(), "total_return": total_return, "max_dd": max_dd,
        "sharpe": sharpe, "avg_r": trades_df["r_multiple"].mean(),
        "trades_df": trades_df, "equity": equity,
    }


def fmt(res) -> str:
    c = res["cfg"]
    return (
        f"{c['direction']:>5} sl={c['sl_atr']:.1f} rr={c['rr']:.1f} adx={c['min_adx']:>2} "
        f"rsi=({c['rsi_buy']:>2},{c['rsi_sell']:>2}) vol={c['vol_mult']:.1f} "
        f"daily={int(c['use_daily'])} | trades={res['trades']:>3} ret={res['total_return']*100:>6.1f}% "
        f"net={res['net']:>9,.0f} PF={res['pf']:>5.2f} WR={res['win_rate']*100:>4.1f}% "
        f"DD={res['max_dd']*100:>5.1f}% avgR={res['avg_r']:>5.2f} "
    )


# ----------------------------------------------------------------- main ----

def load_indicators():
    df1 = load_1m()
    df5 = resample_5m(df1)
    dfd = resample_1d(df1)

    ind = pd.DataFrame(index=df5.index)
    ind["open"], ind["high"], ind["low"], ind["close"], ind["volume"] = (
        df5["open"], df5["high"], df5["low"], df5["close"], df5["volume"]
    )
    ind["ema20"] = ema(df5["close"], 20)
    ind["ema50"] = ema(df5["close"], 50)
    ind["atr"] = atr(df5["high"], df5["low"], df5["close"], 14)
    ind["rsi"] = rsi(df5["close"], 14)
    ind["pdi"], ind["mdi"], ind["adx"] = dmi(df5["high"], df5["low"], df5["close"], 14)
    ind["vwap"] = vwap_day(df5["high"], df5["low"], df5["close"], df5["volume"])
    ind["vol_sma"] = df5["volume"].rolling(20).mean()

    # opening range 09:15-09:45 (previous full day's value, valid for the whole day)
    day = ind.index.date
    or_high, or_low = pd.Series(np.nan, index=ind.index), pd.Series(np.nan, index=ind.index)
    for d in pd.unique(day):
        m = ind.index.date == d
        hm = [(t.hour, t.minute) for t in ind.index[m]]
        orm = [(9, 15) <= x <= (9, 45) for x in hm]
        if any(orm):
            or_high.loc[m] = ind["high"].loc[m][orm].max()
            or_low.loc[m] = ind["low"].loc[m][orm].min()
    ind["or_high"], ind["or_low"] = or_high.ffill(), or_low.ffill()
    ind["post_or"] = pd.Series([(t.hour, t.minute) > (9, 45) for t in ind.index], index=ind.index)

    # daily EMA20 trend filter (anchored to prior day close to avoid lookahead)
    daily_ema = ema(dfd["close"], 20)
    daily_prev = daily_ema.shift(1).reindex(pd.DatetimeIndex(day).normalize()).values
    ind["daily_ema20"] = pd.Series(daily_prev, index=ind.index).ffill()

    # 1m bars + 5m buckets for intrabar probing
    bars1 = df1
    buckets = {}
    for ts in bars1.index:
        lbl = ts.ceil("5min")
        if lbl in df5.index:
            buckets.setdefault(lbl, []).append(ts)
    return df5, ind, bars1, buckets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="store_true", help="only run the chosen config")
    args = ap.parse_args()

    df5, ind, bars1, buckets = load_indicators()
    all_dates = list(pd.unique(ind.index.date))

    # default config (chosen candidate)
    BASE = {
        "direction": "long", "sl_atr": 1.2, "rr": 2.0, "min_adx": 18,
        "rsi_buy": 50, "rsi_sell": 50, "vol_mult": 1.0, "use_daily": True,
        "entry_start": (9, 50), "entry_end": (14, 30),
    }

    if args.only:
        trades_df, daily = run_strategy(BASE, df5, ind, bars1, buckets)
        res = analyze(BASE, trades_df, daily, all_dates)
        print(fmt(res))
        save(res)
        return

    print("Parameter scan on SBIN 1m (Feb-Aug 2026) ...\n")
    grid = []
    for direction in ("long", "both"):
        for sl_atr, rr in ((1.0, 1.5), (1.2, 2.0), (1.5, 3.0), (1.0, 2.0)):
            for min_adx in (15, 20, 25):
                for use_daily in (True, False):
                    grid.append({**BASE, "direction": direction, "sl_atr": sl_atr,
                                 "rr": rr, "min_adx": min_adx, "use_daily": use_daily})

    results = []
    for cfg in grid:
        trades_df, daily = run_strategy(cfg, df5, ind, bars1, buckets)
        res = analyze(cfg, trades_df, daily, all_dates)
        if res:
            results.append(res)

    results.sort(key=lambda r: (r["net"], r["sharpe"]), reverse=True)
    print(f"{len(results)} configs tested, top 25 by net P&L:\n")
    for r in results[:25]:
        print(" ", fmt(r))

    # also show best by sharpe with >= 30 trades
    print("\nBest by Sharpe (>= 30 trades):")
    filtered = [r for r in results if r["trades"] >= 30]
    for r in sorted(filtered, key=lambda r: r["sharpe"], reverse=True)[:10]:
        print(" ", fmt(r))


def save(res):
    trades_df = res["trades_df"].copy()
    trades_df["pnl"] = trades_df["pnl"].round(2)
    trades_df.to_csv(OUT / "sbin_pine6_trades.csv", index=False)
    print(f"Trades saved: {OUT / 'sbin_pine6_trades.csv'}")

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        equity = res["equity"]
        dd = equity / equity.cummax() - 1.0
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                            vertical_spacing=0.05)
        fig.add_trace(go.Scatter(x=equity.index, y=equity.values, name="Equity",
                                 line=dict(color="#00d4ff")), row=1, col=1)
        fig.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, name="Drawdown",
                                 line=dict(color="#ff5252")), row=2, col=1)
        fig.update_layout(template="plotly_dark",
                          title="SBI Pine6 Intraday - Equity & Drawdown", height=600)
        fig.write_html(OUT / "sbin_pine6.html")
        print(f"Chart saved: {OUT / 'sbin_pine6.html'}")
    except Exception as e:
        print(f"Chart skipped: {e}")


if __name__ == "__main__":
    main()
