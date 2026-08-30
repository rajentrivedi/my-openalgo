import os

import numpy as np
import pandas as pd

from backtesting.scalpel import config, hygiene, signals

TIME_BUCKETS = [
    ("open", "09:15", "09:45"),
    ("morning", "09:45", "11:00"),
    ("midday", "11:00", "14:00"),
    ("afternoon", "14:00", "15:15"),
]

HOLDS = [1, 3, 5, 10, 15]
ROUND_TRIP_BPS = 10000.0 * 2 * config.COST_PER_SIDE
MIN_N = 200


def _tb_idx(hms):
    for i, (_, lo, hi) in enumerate(TIME_BUCKETS):
        if lo <= hms < hi:
            return i + 1
    return 0


def _quantile_edges(values, nb=5):
    non_nan = values[~np.isnan(values)]
    if non_nan.size < MIN_N:
        return None
    return [np.quantile(non_nan, q) for q in np.linspace(0, 1, nb + 1)[1:-1]]


def _quantile_bucket(values, edges):
    return np.digitize(values, edges) + 1


def _bucketed_part(sig, tb, q, a_q, trend, ret):
    valid = ~np.isnan(ret) & (tb > 0) & (q > 0) & (a_q > 0)
    if not valid.any():
        return None
    return pd.DataFrame(
        {
            "sig": np.full(int(valid.sum()), sig),
            "tb": tb[valid],
            "q": q[valid],
            "a_q": a_q[valid],
            "trend": trend[valid],
            "ret": ret[valid],
        }
    )


def edge_expectancy(con, symbols, start_epoch, end_epoch, params=None):
    parts = []
    for symbol in symbols:
        df = hygiene.clean_1m_series(con, symbol, start_epoch, end_epoch)
        if df.empty:
            continue
        df = df[~df["datetime"].dt.strftime("%Y-%m-%d").isin(config.BAD_DAYS)].reset_index(
            drop=True
        )
        vals = signals.build_frame(df, params)
        hms = df["datetime"].dt.strftime("%H:%M")
        tb = hms.map(_tb_idx).to_numpy()
        close = df["close"].to_numpy(dtype=float)
        trend_up = (close > vals["vwap"]).astype(int)
        atr_edges = _quantile_edges(vals["atr1"])
        a_q = (
            _quantile_bucket(vals["atr1"], atr_edges) if atr_edges else np.zeros(len(df), dtype=int)
        )
        day = df["datetime"].dt.strftime("%Y-%m-%d").to_numpy()
        for h in HOLDS:
            fwd = np.full(len(df), np.nan)
            for d in pd.unique(day):
                m = day == d
                sub_close = close[m]
                fwd[m] = np.log(
                    np.concatenate([sub_close[h:], np.full(min(h, len(sub_close)), np.nan)])
                ) - np.log(sub_close)
            blocks = []
            for sig in signals.SIGNALS:
                edges = _quantile_edges(vals[sig])
                if edges is None:
                    continue
                q = _quantile_bucket(vals[sig], edges)
                b = _bucketed_part(sig, tb, q, a_q, trend_up, fwd)
                if b is not None:
                    blocks.append(b)
            if not blocks:
                continue
            sym_long = pd.concat(blocks, ignore_index=True)
            agg = (
                sym_long.groupby(["sig", "tb", "q", "a_q", "trend"], as_index=False)
                .agg(
                    n=("ret", "size"),
                    sw=("ret", lambda x: float(x[x > 0].sum())),
                    sl=("ret", lambda x: float(-x[x < 0].sum())),
                    gw=("ret", lambda x: float((x > 0).sum())),
                )
                .assign(hold=h)
            )
            parts.append(agg)
    if not parts:
        return pd.DataFrame()
    long = pd.concat(parts, ignore_index=True)
    agg = long.groupby(["sig", "tb", "q", "a_q", "trend", "hold"], as_index=False).agg(
        n=("n", "sum"),
        sw=("sw", "sum"),
        sl=("sl", "sum"),
        gw=("gw", "sum"),
    )
    agg["gross_bps"] = (agg["sw"] - agg["sl"]) / agg["n"] * 10000.0
    agg["hit"] = agg["gw"] / agg["n"]
    agg["pf"] = np.where(agg["sl"] > 0, agg["sw"] / agg["sl"], np.inf)
    agg["net_bps"] = np.where(agg["hold"] >= 3, agg["gross_bps"] - ROUND_TRIP_BPS, np.nan)
    tb_names = {i + 1: name for i, (name, _, _) in enumerate(TIME_BUCKETS)}
    agg["time_bucket"] = agg["tb"].map(tb_names)
    agg = agg[agg["n"] >= MIN_N]
    return agg.drop(columns=["tb"])


def run_study(con, top_symbols, start_epoch, end_epoch, out_dir=None):
    out_dir = out_dir or config.STUDY_DIR
    os.makedirs(os.path.join(out_dir, "edge_maps"), exist_ok=True)
    grid = edge_expectancy(con, top_symbols, start_epoch, end_epoch)
    grid.to_csv(os.path.join(out_dir, "edge_grid.csv"), index=False)

    rows = []
    for sig in signals.SIGNALS:
        sub = grid[grid["sig"] == sig]
        if sub.empty:
            continue
        sub_net = sub[sub["net_bps"].notna()]
        if sub_net.empty:
            continue
        br = sub_net.sort_values("net_bps", ascending=False).iloc[0]
        rows.append(
            {
                "signal": sig,
                "hold": int(br["hold"]),
                "time_bucket": br["time_bucket"],
                "q": int(br["q"]),
                "a_q": int(br["a_q"]),
                "trend": int(br["trend"]),
                "n": int(br["n"]),
                "gross_bps": round(float(br["gross_bps"]), 3),
                "net_bps": round(float(br["net_bps"]), 3),
                "hit": round(float(br["hit"]), 4),
                "pf": round(float(br["pf"]), 3),
            }
        )
    bests = pd.DataFrame(rows).sort_values(["net_bps"], ascending=False).reset_index(drop=True)
    bests.to_csv(os.path.join(out_dir, "edge_best_cells.csv"), index=False)

    with open(os.path.join(out_dir, "edge-summary.md"), "w") as f:
        f.write("# Discovery edge-map summary\n\n")
        f.write(
            "Window: first 12 months, 60 most-liquid NIFTY 500 symbols.\n"
            "Net = gross forward return minus 20 bps round-trip cost (applied for hold>=3).\n"
            "Forward returns computed within the same trading day only.\n\n"
        )
        f.write(
            "## Best cell per signal (max net bps across time-bucket x quantile x ATR-quintile x trend)\n\n"
        )
        f.write(
            "| signal | hold | tb | q | atr_q | trend | n | gross bps | net bps | hit | PF |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
        )
        for _, r in bests.iterrows():
            f.write(
                f"| {r['signal']} | {int(r['hold'])} | {r['time_bucket']} | {int(r['q'])} | {int(r['a_q'])} | {r['trend']} | {int(r['n'])} | {r['gross_bps']:.3f} | {r['net_bps']:.3f} | {r['hit']:.4f} | {r['pf']:.3f} |\n"
            )
    return grid, bests
