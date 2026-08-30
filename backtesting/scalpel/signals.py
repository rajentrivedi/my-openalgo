import numpy as np
import pandas as pd

from backtesting.scalpel import config

SIGNALS = ["vwap_z", "rsi2", "bbpct", "roll_z", "or_fade", "rvol"]


def _atr(high, low, close, length):
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(
        axis=1
    )
    return tr.ewm(alpha=1.0 / length, adjust=False).mean()


def _atr_5m(d, length):
    bucket = (d["seq"] // 5).astype(int)
    g = d.groupby(bucket)
    h5, l5, c5 = g["high"].max(), g["low"].min(), g["close"].last()
    atr5_bucket = _atr(h5, l5, c5, length)
    atr5_prev = atr5_bucket.shift(1)
    return bucket.map(atr5_prev)


def build_frame(df, params=None):
    p = {**config.SIGNAL_PARAMS, **(params or {})}
    d = df.reset_index(drop=True).copy()
    d["date"] = d["datetime"].dt.date
    d["seq"] = d.groupby("date").cumcount()
    close = d["close"].astype(float)
    high = d["high"].astype(float)
    low = d["low"].astype(float)
    vol = d["volume"].astype(float)
    tp = (high + low + close) / 3.0
    grp = d["date"]

    vwap = (tp * vol).groupby(grp).cumsum() / vol.groupby(grp).cumsum()
    vwap_z = (close - vwap) / close * 100.0

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / p["rsi_len"], adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / p["rsi_len"], adjust=False).mean()
    rsi2 = pd.Series(np.nan, index=d.index)
    up = (avg_loss == 0) & (avg_gain > 0)
    dn = (avg_gain == 0) & (avg_loss > 0)
    both = (avg_gain > 0) & (avg_loss > 0)
    rsi2[up] = 100.0
    rsi2[dn] = 0.0
    rsi2[both] = 100.0 - 100.0 / (1.0 + avg_gain[both] / avg_loss[both])

    mid = close.rolling(p["bb_len"]).mean().shift(1)
    std = close.rolling(p["bb_len"]).std(ddof=0).shift(1)
    upper = mid + p["bb_std"] * std
    lower = mid - p["bb_std"] * std
    bbpct = ((close - lower) / (upper - lower)).where((upper - lower) > 0)

    rmean = close.rolling(p["roll_len"]).mean().shift(1)
    rstd = close.rolling(p["roll_len"]).std(ddof=0).shift(1)
    roll_z = ((close - rmean) / rstd).where(rstd > 0)

    or_zone = d["seq"] < p["or_len"]
    zero_array = np.zeros(len(d))
    or_high = high.where(or_zone, zero_array).expanding().max()
    or_low = low.where(or_zone, np.inf).expanding().min()
    or_high = or_high.where(or_zone.expanding().sum() > 0)
    or_low = or_low.where(or_zone.expanding().sum() > 0)

    atr5 = _atr_5m(d, p["atr_len"])
    rvol = vol / vol.rolling(p["rvol_len"]).mean().shift(1)

    or_fade = (close - or_low) / atr5

    out = {
        "vwap_z": vwap_z.to_numpy(),
        "rsi2": rsi2.to_numpy(),
        "bbpct": bbpct.to_numpy(),
        "roll_z": roll_z.to_numpy(),
        "or_fade": or_fade.to_numpy(),
        "rvol": rvol.to_numpy(),
        "atr1": _atr(high, low, close, p["atr_len"]).to_numpy(),
        "atr5": atr5.to_numpy(),
        "or_high": or_high.to_numpy(),
        "or_low": or_low.to_numpy(),
    }
    for k, v in out.items():
        out[k] = np.asarray(v, dtype=float)
    return out
