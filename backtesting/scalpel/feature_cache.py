import os

import pandas as pd

from backtesting.scalpel import config, hygiene, loader

FEATURE_CACHE_PATH = os.path.join(config.CACHE_DIR, "wf_features.parquet")
UNIVERSE_TOP_N = 100


def _per_symbol_daily_features(full):
    d = full.copy()
    d["date"] = d["datetime"].dt.strftime("%Y-%m-%d")
    d["time"] = d["datetime"].dt.strftime("%H:%M")
    d = d.sort_values("datetime").reset_index(drop=True)
    d["ret"] = d.groupby("date")["close"].pct_change()
    d["notional"] = d["volume"] * d["close"]
    g = d.groupby("date")
    d["vol_ratio"] = d["volume"] / g["volume"].transform("mean")

    dly = d.groupby("date").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last")
    )
    dly["prev_close"] = dly["close"].shift(1)
    dly["close_21"] = dly["close"].shift(20)
    dly["prev_gap_pct"] = (dly["open"] / dly["prev_close"] - 1.0) * 100.0
    dly["prev_trend_pct"] = (dly["close"] / dly["close_21"] - 1.0) * 100.0
    dly["prev_close_to_high"] = dly["close"] / dly["high"]

    intra = g.agg(
        prev_vol_pct=("ret", lambda s: s.std() * 100.0),
        prev_rvol_median=("vol_ratio", "median"),
        prev_notional_median=("notional", "median"),
        prev_daily_notional_cr=("notional", "sum"),
    )
    intra["prev_daily_notional_cr"] = intra["prev_daily_notional_cr"] / 1e7

    sixth_open = d.loc[d.groupby("date")["open"].cumcount() == 5].set_index("date")["open"]
    first_open = d.groupby("date")["open"].first()
    dly["prev_ret_5m"] = (sixth_open / first_open - 1.0).reindex(dly.index)

    out = dly.join(intra)
    out["symbol"] = full["symbol"].iloc[0]
    return out.reset_index()


def _universe(con, top_n):
    medians = pd.Series(loader.notional_medians(con, config.RESEARCH_START, config.RESEARCH_END))
    return medians.nlargest(top_n).index.tolist()


def compute_daily_features(con, rebuild=False, top_n=UNIVERSE_TOP_N, cache_path=None):
    path = FEATURE_CACHE_PATH if cache_path is None else cache_path
    if (not rebuild) and os.path.exists(path):
        return pd.read_parquet(path)
    cal = loader.trading_calendar(con)
    date_set = {d.date().isoformat() for d in cal}
    rows = []
    for sym in _universe(con, top_n):
        full = hygiene.clean_1m_series(con, sym, config.RESEARCH_START, config.RESEARCH_END)
        if len(full) < 300:
            continue
        f = _per_symbol_daily_features(full)
        f = f[f["date"].isin(date_set)]
        if f.empty:
            continue
        rows.append(f)
    out = pd.concat(rows, ignore_index=True)
    out["prev_daily_notional_cr"] = out["prev_daily_notional_cr"].clip(upper=1e6)
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    out.to_parquet(path, index=False)
    return out


def features_for_trading_day(feature_table, day_iso):
    out = feature_table[feature_table["date"] == day_iso].copy()
    if out.empty:
        return pd.DataFrame(columns=loader.FEATURE_COLS)
    out["prev_date"] = pd.Timestamp(day_iso)
    out["is_liquid"] = out["prev_daily_notional_cr"] >= config.LIQUIDITY_GATE_CR
    return out[loader.FEATURE_COLS].sort_values("symbol").reset_index(drop=True)
