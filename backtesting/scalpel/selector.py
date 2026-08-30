import numpy as np
import pandas as pd

from backtesting.scalpel.loader import FEATURE_COLS

SCORE_COLS = [
    "prev_vol_pct",
    "prev_gap_pct",
    "prev_close_to_high",
    "prev_rvol_median",
    "prev_daily_notional_cr",
]

WEIGHTS = {
    "prev_vol_pct": 1.0,
    "prev_gap_pct": 0.7,
    "prev_close_to_high": 0.5,
    "prev_rvol_median": 0.4,
    "prev_daily_notional_cr": 0.2,
}


def rule_string():
    terms = " + ".join(f"{w:.1f}*z({c})" for c, w in WEIGHTS.items())
    return (
        "score = " + terms + "; "
        "where z(x) is cross-sectional standard score of x over that day's tradable pool; "
        "rank desc, tie-break by descending prev_daily_notional_cr then symbol asc. "
        "Only symbols with is_liquid=True participate."
    )


def _score(df):
    pool = df[df["is_liquid"]].copy()
    if pool.empty:
        return pd.DataFrame(columns=["symbol", "score", "notional"])
    scored = pd.DataFrame({"symbol": pool["symbol"], "notional": pool["prev_daily_notional_cr"]})
    score = np.zeros(len(pool))
    for col, w in WEIGHTS.items():
        v = pool[col].astype(float)
        mu = v.mean()
        sd = v.std()
        z = (v - mu) / sd if sd and sd == sd else np.zeros(len(pool))
        score = score + w * np.nan_to_num(z)
    scored["score"] = score
    return scored


def rank_symbols(features_df, top_n=3):
    if (
        features_df is None
        or features_df.empty
        or not set(FEATURE_COLS).issubset(features_df.columns)
    ):
        return []
    scored = _score(features_df)
    if scored.empty:
        return []
    ranked = scored.sort_values(["score", "notional", "symbol"], ascending=[False, False, True])
    return ranked["symbol"].tolist()[:top_n]
