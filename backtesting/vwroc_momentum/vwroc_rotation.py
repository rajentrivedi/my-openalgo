"""
VWROC Cross-Sectional Rank-Rotation Portfolio Engine.

Every rebalance day, score the basket by VWROC(lookback), rank descending,
equal-weight the top-N. Uses vectorbt `from_order_func` with a rebalance
order function and `segment_mask` so orders fire ONLY on the chosen rebalance
days (monthly/weekly), with shared cash -> capital truly rotates: names dropping
out of the top-N are sold and proceeds buy names entering the top-N.

Long-only. Cash (unallocated weight) stays idle. Indian delivery fees,
whole-share granularity. Benchmarked vs NIFTY 50 and vs a no-rotation
equal-weight whole-basket portfolio on the SAME monthly cadence, so the
comparison isolates whether *selecting* top-N adds value over holding everything.

Usage:
    venv/bin/python backtesting/vwroc_momentum/vwroc_rotation.py [--top-n 3] [--rebalance M] ...
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
import yfinance as yf
from numba import njit

from vectorbt.portfolio import nb

script_dir = Path(__file__).resolve().parent

BASKET = [
    "SBIN.NS", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "TCS.NS", "INFY.NS", "KOTAKBANK.NS", "AXISBANK.NS",
]
INIT_CASH = 1_000_000
FEES = 0.00111
FIXED_FEES = 20
YEARS = 5

parser = argparse.ArgumentParser(description="VWROC rank rotation portfolio engine")
parser.add_argument("--lookback", type=int, default=15)
parser.add_argument("--top-n", type=int, default=3)
parser.add_argument("--rebalance", choices=["M", "W"], default="M",
                    help="M=monthly, W=weekly rebalance")
parser.add_argument("--cache", action="store_true", help="cache yfinance frames to parquet")
args = parser.parse_args()

LOOKBACK = args.lookback
TOP_N = args.top_n
REBAL = "ME" if args.rebalance == "M" else "W"


# ---- vectorbt rebalance order function (rebalance to target weights only on segment_mask days) ----
@njit
def pre_group_func_nb(c):
    order_value_out = np.empty(c.group_len)
    return (order_value_out,)


@njit
def pre_segment_func_nb(c, order_value_out, size, price, size_type, direction):
    for col in range(c.from_col, c.to_col):
        c.last_val_price[col] = nb.get_col_elem_nb(c, col, price)
    nb.sort_call_seq_nb(c, size, size_type, direction, order_value_out)
    return ()


@njit
def order_func_nb(c, size, price, size_type, direction, fees, fixed_fees, slippage):
    return nb.order_nb(
        size=nb.get_elem_nb(c, size),
        price=nb.get_elem_nb(c, price),
        size_type=nb.get_elem_nb(c, size_type),
        direction=nb.get_elem_nb(c, direction),
        fees=nb.get_elem_nb(c, fees),
        fixed_fees=nb.get_elem_nb(c, fixed_fees),
        slippage=nb.get_elem_nb(c, slippage),
    )


def rebalance_portfolio(close, target_weights, segment_mask):
    """Run a target-weight portfolio, trading only where segment_mask is True.

    NOTE: target weights must be a plain numpy 2D array -- passing a pandas
    DataFrame as the order-func `size` arg trips a vectorbt 1.0/numba typing bug
    on this stack (Python 3.14). The row index is aligned to `close` externally.
    """
    pf = vbt.Portfolio.from_order_func(
        close,
        order_func_nb,
        target_weights,
        vbt.Rep("price"),
        vbt.Rep("size_type"),
        vbt.Rep("direction"),
        vbt.Rep("fees"),
        vbt.Rep("fixed_fees"),
        vbt.Rep("slippage"),
        segment_mask=segment_mask,
        pre_group_func_nb=pre_group_func_nb,
        pre_segment_func_nb=pre_segment_func_nb,
        pre_segment_args=(
            target_weights,
            vbt.Rep("price"),
            vbt.Rep("size_type"),
            vbt.Rep("direction"),
        ),
        broadcast_named_args=dict(
            price=close,
            size_type=vbt.portfolio.enums.SizeType.TargetPercent,
            direction=vbt.portfolio.enums.Direction.LongOnly,
            fees=FEES,
            fixed_fees=FIXED_FEES,
            slippage=0.0,
        ),
        init_cash=INIT_CASH,
        cash_sharing=True,
        group_by=True,
        freq="1D",
    )
    return pf


def fetch_many(symbols, use_cache=False):
    cache = script_dir / "rotation_data"
    cache.mkdir(exist_ok=True)
    data = {}
    for sym in symbols:
        cache_f = cache / f"{sym.replace('.', '_')}.parquet"
        if use_cache and cache_f.exists():
            df = pd.read_parquet(cache_f)
        else:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=365 * (YEARS + 1))
            df = yf.download(sym, start=start_date, end=end_date, interval="1d", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_convert(None)
            if use_cache:
                df.to_parquet(cache_f)
        data[sym] = df
    return data


def vwroc(close, volume, lookback):
    price_chg = close.diff()
    return (price_chg * volume).rolling(lookback).sum() / volume.rolling(lookback).sum() / close * 100.0


def main():
    print(f"Fetching {len(BASKET)} names ({YEARS}+1y daily)...")
    data = fetch_many(BASKET, use_cache=args.cache)

    # Common trading-day index (intersection of all names' dates)
    common_index = None
    for sym, df in data.items():
        idx = df.index
        common_index = idx if common_index is None else common_index.intersection(idx)
    common_index = common_index.sort_values()

    for sym in list(data.keys()):
        df = data[sym].reindex(common_index)
        df[["Open", "High", "Low", "Close", "Volume"]] = (
            df[["Open", "High", "Low", "Close", "Volume"]].ffill())
        data[sym] = df

    close = pd.DataFrame({sym: data[sym]["Close"] for sym in BASKET})
    volume = pd.DataFrame({sym: data[sym]["Volume"] for sym in BASKET})

    # VWROC score per name
    score_df = pd.DataFrame({sym: vwroc(close[sym], volume[sym], LOOKBACK) for sym in BASKET})
    ranks = score_df.rank(axis=1, ascending=False, method="first")

    # Target weights: set on rebalance days only, else ffill (so vectorbt trades only at rebal).
    weights = pd.DataFrame(np.nan, index=common_index, columns=BASKET)
    rebal_days = close.resample(REBAL).last().index
    rebal_days = rebal_days[rebal_days > common_index[0] + pd.Timedelta(days=LOOKBACK + 5)]
    for day in rebal_days:
        if day not in common_index:
            day = close.index.asof(day)
        if day not in weights.index:
            continue
        day_ranks = ranks.loc[day]
        top = list(day_ranks[day_ranks <= TOP_N].index)
        # Only names with enough history to have a valid VWROC score are eligible
        top = [s for s in top if not np.isnan(score_df.loc[day, s])]
        if top:
            w = 1.0 / len(top)
            for s in top:
                weights.loc[day, s] = w
        else:
            for s in BASKET:
                weights.loc[day, s] = 0.0

    # First valid rebalance anchors the portfolio (avoid huge initial buy from NaN).
    first_rebal = weights.index[weights.notna().any(axis=1)][0] if weights.notna().any(axis=1).any() else common_index[0]
    weights = weights.loc[first_rebal:]
    weights_full = weights.ffill().fillna(0.0)

    # segment_mask: rebalance ONLY where the target changed (i.e. actual rebalance days).
    # We mark each row where the weight vector differs from the previous row.
    w_row = weights_full.reset_index(drop=True)
    changed = (w_row != w_row.shift(1).fillna(w_row.iloc[0])).any(axis=1).values
    changed[0] = True  # initial framing
    segment_mask = pd.Series(changed, index=weights_full.index)

    close_panel = close.loc[weights_full.index]

    # ---- Strategy portfolio (rotation) ----
    seg_2d = segment_mask.to_numpy().reshape(-1, 1)
    pf = rebalance_portfolio(close_panel, weights_full.to_numpy(dtype=float), seg_2d)

    # ---- baseline: equal-weight whole-basket, rebalanced on the SAME cadence ----
    hold_weights = pd.DataFrame(1.0 / len(BASKET), index=weights_full.index, columns=BASKET)
    pf_hold = rebalance_portfolio(close_panel, hold_weights.to_numpy(dtype=float), seg_2d)

    # ---- benchmark: NIFTY 50 ----
    nifty = yf.download("^NSEI", start=weights_full.index[0], end=weights_full.index[-1] + pd.Timedelta(days=1),
                        interval="1d", progress=False)
    if isinstance(nifty.columns, pd.MultiIndex):
        nifty.columns = nifty.columns.get_level_values(0)
    nifty.index = pd.to_datetime(nifty.index)
    nifty = nifty.sort_index()
    bench = nifty["Close"].reindex(weights_full.index).ffill().bfill()
    pf_bench = vbt.Portfolio.from_holding(bench, init_cash=INIT_CASH, fees=FEES, freq="1D")

    def stats(p):
        return {
            "Total Return": p.total_return() * 100,
            "Sharpe": p.sharpe_ratio(),
            "Max DD": p.max_drawdown() * 100,
            "Trades": int(p.trades.count()),
            "Win Rate %": (p.trades.records_readable["PnL"] > 0).mean() * 100 if p.trades.count() else float("nan"),
        }

    out = pd.DataFrame({
        f"Rotation top-{TOP_N} ({args.rebalance})": stats(pf),
        "Hold whole basket": stats(pf_hold),
        "NIFTY 50": stats(pf_bench),
    }).T
    rot_label = f"Rotation top-{TOP_N} ({args.rebalance})"

    print("\n" + "=" * 78)
    print(f"VWROC ROTATION  lb={LOOKBACK} top-{TOP_N} rebal={args.rebalance}  (shared cash, delivery fees)")
    print("=" * 78)
    print(out.to_string(float_format=lambda x: f"{x:.2f}"))

    # Rotation-added-value: strategy vs holding the whole basket
    rot_ret, hold_ret = out.iloc[0, 0], out.iloc[1, 0]
    delta = rot_ret - hold_ret
    print(f"\nRotation ({rot_label}):   {rot_ret:+.2f}%  total return")
    print(f"Hold whole basket:          {hold_ret:+.2f}%  total return")
    print(f"Rotation adds {delta:+.2f}pct vs equal-weight whole-basket hold"
          f" ({'EDGE' if delta > 0 else 'NO EDGE'}).")

    pf.trades.records_readable.to_csv(
        script_dir / f"rotation_top{TOP_N}_lb{LOOKBACK}_{args.rebalance}_trades.csv", index=False)

    fig = pf.plot(subplots=["value", "underwater", "cum_returns"], template="plotly_dark",
                  title=f"VWROC Rank Rotation top-{TOP_N} lb={LOOKBACK} ({args.rebalance})")
    fig.write_html(str(script_dir / f"rotation_top{TOP_N}_lb{LOOKBACK}_{args.rebalance}.html"),
                   include_plotlyjs="cdn")
    print(f"\nChart: rotation_top{TOP_N}_lb{LOOKBACK}_{args.rebalance}.html")


if __name__ == "__main__":
    main()