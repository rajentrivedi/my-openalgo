from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtesting.scalpel import signals

DEFAULT_PARAMS = {
    "bbpct_thr": 0.0,
    "rsi_thr": 10.0,
    "vwap_z_thr": 0.0,
    "min_rvol": 1.0,
    "proftrigger_r": 1.0,
    "trail_r": 0.5,
    "stop_r": 1.0,
    "hard_exit": "15:15",
    "max_hold_bars": 60,
}


@dataclass
class Analysis:
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    equity: pd.Series = field(default_factory=pd.Series)
    ret_pct: float = 0.0
    pf: float = 0.0
    expectancy: float = 0.0
    n_trades: int = 0
    max_dd: float = 0.0


def _entry_mask(vals, df, p):
    rsi = vals["rsi2"]
    bb = vals["bbpct"]
    vz = vals["vwap_z"]
    rv = vals["rvol"]
    d = df.reset_index(drop=True)
    d["date"] = d["datetime"].dt.date
    seq = d.groupby("date").cumcount().to_numpy()
    long_ok = (
        (bb <= p["bbpct_thr"])
        & (rsi <= p["rsi_thr"])
        & (vz >= p["vwap_z_thr"])
        & (rv >= p["min_rvol"])
    )
    short_ok = (
        (bb >= 1.0 - p["bbpct_thr"])
        & (rsi >= 100.0 - p["rsi_thr"])
        & (vz <= -p["vwap_z_thr"])
        & (rv >= p["min_rvol"])
    )
    warm = np.asarray(seq, dtype=float) >= 15
    valid = ~np.isnan(bb) & ~np.isnan(rsi) & ~np.isnan(vz) & ~np.isnan(rv) & warm
    mask = np.zeros(len(df), dtype=int)
    mask[valid & long_ok] = 1
    mask[valid & short_ok] = -1
    return mask


def run_strategy(df, params=None, cost=0.001):
    p = {**DEFAULT_PARAMS, **(params or {})}
    df = df.reset_index(drop=True)
    n = len(df)
    if n == 0:
        return Analysis()
    vals = signals.build_frame(df, p)
    mask = _entry_mask(vals, df, p)

    dt = df["datetime"].to_numpy()
    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr5 = vals["atr5"]
    hm = df["datetime"].dt.strftime("%H:%M").to_numpy()

    trades = []
    equity = np.ones(n)
    cum_ret = 0.0
    pos = 0
    entry_bar = -1
    entry_price = 0.0
    stop_dist = 0.0
    stop_level = 0.0
    trigger_dist = 0.0
    trail_ref = 0.0
    be_done = False

    for k in range(n - 1):
        if pos != 0:
            hit_stop = (low[k] <= stop_level) if pos > 0 else (high[k] >= stop_level)
            hard = hm[k] >= p["hard_exit"]
            held = (k - entry_bar) >= p["max_hold_bars"]
            if hit_stop or hard or held:
                if hit_stop:
                    exit_price = stop_level
                    exit_reason = "stop"
                elif hard:
                    exit_price = close[k]
                    exit_reason = "hard_exit"
                else:
                    exit_price = close[k]
                    exit_reason = "max_hold"
                r = ((exit_price - entry_price) / stop_dist) * pos
                gross = ((exit_price / entry_price) - 1.0) * pos
                ret = gross - 2.0 * cost
                cum_ret += ret
                trades.append(
                    {
                        "symbol": df["symbol"].iloc[0],
                        "side": pos,
                        "entry_ts": pd.Timestamp(dt[entry_bar + 1]),
                        "exit_ts": pd.Timestamp(dt[k]),
                        "entry": entry_price,
                        "exit": exit_price,
                        "stop_dist": stop_dist,
                        "r": r,
                        "ret_pct": ret,
                        "exit_reason": exit_reason,
                        "bars_held": k - entry_bar,
                    }
                )
                pos = 0
        if pos != 0:
            if pos > 0:
                if close[k] > trail_ref:
                    trail_ref = close[k]
                if not be_done and (close[k] - entry_price) >= trigger_dist:
                    be_done = True
                if be_done:
                    stop_level = max(stop_level, entry_price, trail_ref - p["trail_r"] * stop_dist)
            else:
                if close[k] < trail_ref:
                    trail_ref = close[k]
                if not be_done and (entry_price - close[k]) >= trigger_dist:
                    be_done = True
                if be_done:
                    stop_level = min(stop_level, entry_price, trail_ref + p["trail_r"] * stop_dist)
        if pos == 0 and mask[k] != 0:
            pos = int(mask[k])
            entry_bar = k
            entry_price = open_[k + 1]
            sd = atr5[k]
            if not (np.isfinite(sd) and sd > 0):
                sd = abs(close[k]) * 0.002
            stop_dist = sd
            trigger_dist = p["proftrigger_r"] * stop_dist
            trail_ref = entry_price
            stop_level = entry_price - pos * stop_dist
            be_done = False
        equity[k] = 1.0 + cum_ret
    equity[n - 1] = 1.0 + cum_ret

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return Analysis(trades=tdf, equity=pd.Series(equity), n_trades=0)
    wins = tdf[tdf["ret_pct"] > 0]["ret_pct"].sum()
    losses = tdf[tdf["ret_pct"] < 0]["ret_pct"].sum()
    pf = wins / -losses if losses < 0 else float("inf")
    exp = tdf["r"].mean()
    cum = np.concatenate([[0.0], np.cumsum(tdf["ret_pct"])])
    peak = np.maximum.accumulate(cum)
    dd = float(np.max(peak - cum))
    return Analysis(
        trades=tdf,
        equity=pd.Series(equity, index=df["datetime"].to_numpy()),
        ret_pct=float(tdf["ret_pct"].sum() * 100.0),
        pf=float(pf),
        expectancy=float(exp),
        n_trades=len(tdf),
        max_dd=dd,
    )
