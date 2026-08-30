import numpy as np
import pandas as pd

from backtesting.scalpel import config, engine, feature_cache, hygiene, loader, selector

DEFAULT_GRID = [
    {
        "bbpct_thr": b,
        "rsi_thr": r,
        "proftrigger_r": pt,
        "trail_r": tr,
        "min_rvol": 1.0,
    }
    for b in (0.0,)
    for r in (5.0, 15.0, 25.0)
    for pt in (1.0, 2.0)
    for tr in (0.5, 1.0)
]

N_FOLDS = 6
TRAIN_MONTHS = 6
VAL_MONTHS = 3
TOP_N = 3
MIN_TRAIN_TRADES = 30


def _month_offset(date_iso, months):
    ts = pd.Timestamp(date_iso)
    return (ts + pd.DateOffset(months=months)).date().isoformat()


def fold_splits(cal):
    cal = sorted(cal)
    folds = []
    for i in range(N_FOLDS):
        train_start = _month_offset(cal[0], i * 3)
        train_end = _month_offset(train_start, TRAIN_MONTHS)
        val_end = _month_offset(train_end, VAL_MONTHS)
        train = [d for d in cal if train_start <= d < train_end]
        val = [d for d in cal if train_end <= d < val_end]
        if len(train) == 0:
            continue
        folds.append(
            {
                "fold": i,
                "train_start": train_start,
                "train_end": train_end,
                "val_end": val_end,
                "train": train,
                "val": val,
            }
        )
    return folds


def _prev_day(cal, date_iso):
    cal = sorted(cal)
    i = cal.index(date_iso)
    return None if i == 0 else cal[i - 1]


def _symbols_for_day(features, date_iso, top_n=TOP_N):
    return selector.rank_symbols(features, top_n=top_n)


CON = None
_SYM_MEM = {}


def _preload_frames(features, cal, dates, top_n=TOP_N):
    frames = {}
    for date_iso in dates:
        prev = _prev_day(cal, date_iso)
        if prev is None:
            continue
        feats = feature_cache.features_for_trading_day(features, prev)
        for sym in selector.rank_symbols(feats, top_n=top_n):
            if (sym, date_iso) in frames:
                continue
            if sym not in _SYM_MEM:
                _SYM_MEM[sym] = hygiene.clean_1m_series(
                    CON, sym, config.RESEARCH_START, config.RESEARCH_END
                )
            full = _SYM_MEM[sym]
            day = full[full["datetime"].dt.strftime("%Y-%m-%d") == date_iso]
            if len(day) < 30:
                continue
            vals = engine.signals.build_frame(day.reset_index(drop=True), engine.DEFAULT_PARAMS)
            frames[(sym, date_iso)] = (day.reset_index(drop=True), vals)
    return frames


def _score_trades(trades):
    if len(trades) == 0:
        return {"n": 0, "pf": 0.0, "expectancy": 0.0, "net_ret": 0.0, "win_rate": 0.0}
    wins = trades.loc[trades["ret_pct"] > 0, "ret_pct"].sum()
    losses = trades.loc[trades["ret_pct"] < 0, "ret_pct"].sum()
    pf = wins / -losses if losses < 0 else float("inf")
    return {
        "n": len(trades),
        "pf": pf,
        "expectancy": float(trades["r"].mean()),
        "net_ret": float(trades["ret_pct"].sum() * 100.0),
        "win_rate": float((trades["ret_pct"] > 0).mean()),
    }


def _run_frames(frames, days, params, cost):
    out = []
    for (sym, date_iso), (day, vals) in frames.items():
        if date_iso not in days:
            continue
        res = engine.run_with_frame(day, vals, params, cost=cost)
        if not res.trades.empty:
            tr = res.trades.copy()
            tr["symbol"] = sym
            tr["date"] = date_iso
            out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _grid_search(frames, train_days, grid, cost):
    best = None
    best_score = -1e9
    for params in grid:
        trades = _run_frames(frames, train_days, params, cost)
        s = _score_trades(trades)
        if s["n"] < MIN_TRAIN_TRADES:
            continue
        score = s["expectancy"] * min(s["n"], 200) if s["expectancy"] > 0 else s["expectancy"]
        if score > best_score:
            best_score = score
            best = {"params": params, **s}
    return best


def walk_forward(con, features=None, params_grid=None, cost=None, verbose=True):
    global CON
    CON = con
    _SYM_MEM.clear()
    if params_grid is None:
        params_grid = DEFAULT_GRID
    cost = config.COST_PER_SIDE if cost is None else cost
    if features is None:
        features = feature_cache.compute_daily_features(con)
    cal = [d.date().isoformat() for d in loader.trading_calendar(con)]
    folds = fold_splits(cal)
    results = []
    oos_trades = []
    fold_frames = {}
    for fold in folds:
        frames = _preload_frames(features, cal, fold["train"] + fold["val"])
        fold_frames[fold["fold"]] = frames
        fit = _grid_search(frames, fold["train"], params_grid, cost)
        if fit is None:
            fold_out = pd.DataFrame()
            report = {
                **fold,
                "params": None,
                "train": _score_trades(pd.DataFrame()),
                "val": _score_trades(fold_out),
            }
        else:
            fold_out = _run_frames(frames, fold["val"], fit["params"], cost)
            report = {
                **fold,
                "params": fit["params"],
                "train": fit,
                "val": _score_trades(fold_out),
                "train_dates": fold["train"],
                "val_dates": fold["val"],
            }
        results.append(report)
        if not fold_out.empty:
            fold_out = fold_out.copy()
            fold_out["fold"] = fold["fold"]
            oos_trades.append(fold_out)
        if verbose:
            tag = "fit" if fit else "NO-FIT"
            print(
                f"fold {fold['fold']}: train[{fold['train_start']}..{fold['train_end']}) "
                f"val[{fold['train_end']}..{fold['val_end']}) n={report['val']['n']} "
                f"pf={report['val']['pf']:.3f} exp={report['val']['expectancy']:.4f} {tag}"
            )
    all_oos = pd.concat(oos_trades, ignore_index=True) if oos_trades else pd.DataFrame()

    perturbed = []
    for fold in results:
        fp = fold["params"]
        if not fp:
            continue
        for key in ("bbpct_thr", "rsi_thr", "proftrigger_r", "trail_r", "min_rvol"):
            base = fp.get(key)
            if base is None:
                continue
            for mult in (0.85, 1.15):
                pp = {**fp, key: base * mult}
                tr = _run_frames(fold_frames.get(fold["fold"], {}), fold["val_dates"], pp, cost)
                s = _score_trades(tr)
                perturbed.append(
                    {
                        "fold": fold["fold"],
                        "param": key,
                        "mult": mult,
                        "n": s["n"],
                        "pf": s["pf"],
                        "expectancy": s["expectancy"],
                    }
                )
        fold_frames.pop(fold["fold"], None)

    oos_s = _score_trades(all_oos)
    per_symbol = (
        all_oos.groupby("symbol")
        .agg(n=("r", "size"), ret=("ret_pct", "sum"))
        .sort_values("n", ascending=False)
        if not all_oos.empty
        else pd.DataFrame()
    )
    per_day = (
        all_oos.groupby("date").agg(n=("r", "size"), ret=("ret_pct", "sum")).sort_index()
        if not all_oos.empty
        else pd.DataFrame()
    )
    return {
        "folds": results,
        "oos_trades": all_oos,
        "oos_pf": oos_s["pf"],
        "oos_expectancy": oos_s["expectancy"],
        "oos_n": oos_s["n"],
        "oos_net_ret": oos_s["net_ret"],
        "oos_win_rate": oos_s["win_rate"],
        "per_symbol": per_symbol,
        "per_day": per_day,
        "robustness": perturbed,
    }
