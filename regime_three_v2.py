"""
Regime-Three V2: Three-mode intraday strategy.
Runs on FULL data (no day-by-day splits), switches modes at day boundaries.

Mode 1 (Trending): Momentum continuation — struct + slope + volume
Mode 2 (Ranging): Mean reversion — fade extreme 20-bar returns
Mode 3 (Neutral): No trade
"""
from __future__ import annotations
import argparse, os, sys, time, copy
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv
load_dotenv(".env", override=True)

from backtest_range_breakout import (
    IST, BacktestParams, compute_nifty_signals, compute_signals,
    load_symbols, load_universe_data, print_data_quality,
    to_ist, in_allowed_session, nifty_at_candle, apply_slippage,
)
# We need to replicate the portfolio loop with regime awareness
# Instead, let's import run_portfolio_backtest and add a hook

from backtest_range_breakout import ta as _ta
import backtest_range_breakout as v3

OUT_DIR = "diagnostics"; os.makedirs(OUT_DIR, exist_ok=True)


def _compute_regime(nifty_sig: pd.DataFrame, ts: pd.Timestamp):
    """Classify regime at timestamp ts using NIFTY data up to ts."""
    if nifty_sig is None or nifty_sig.empty:
        return 2
    prior = nifty_sig[nifty_sig.index <= ts]
    if len(prior) < 50:
        return 2
    last = prior.iloc[-1]
    ema20 = float(last.get("ema20", 0))
    ema50 = float(last.get("ema50", 0))
    slope = float(last.get("ema20_slope", 0))
    atr = float(last.get("atr", 0.5))
    ret20 = (float(last["close"]) / float(prior.iloc[-20]["close"]) - 1) if len(prior) >= 20 else 0

    trending = ema20 > ema50 and slope > 0
    ranging = abs(ret20 * 100) < 2.5 or atr < prior["atr"].median() * 0.8
    if trending:
        return 1
    elif ranging:
        return 2
    return 3


def run_regime_backtest(raw, nifty_df, p: BacktestParams):
    """Full portfolio backtest with per-day regime switching."""
    # Precompute signals for all symbols (base features)
    signals_by_symbol = {}
    for sym, df in raw.items():
        sig = compute_signals(df, p)
        # Add 20-bar return for mean reversion
        ret20 = sig["close"].pct_change(20)
        sig["ret20_pct"] = ret20
        signals_by_symbol[sym] = sig

    nifty_sig = compute_nifty_signals(nifty_df, p) if nifty_df is not None else None
    # Add regime features to NIFTY
    if nifty_sig is not None:
        nifty_sig["atr"] = _ta.atr(nifty_sig["high"], nifty_sig["low"], nifty_sig["close"], 14)
        nifty_sig["ema20"] = nifty_sig["close"].ewm(span=20, adjust=False).mean()
        nifty_sig["ema50"] = nifty_sig["close"].ewm(span=50, adjust=False).mean()
        nifty_sig["ema20_slope"] = (nifty_sig["ema20"] - nifty_sig["ema20"].shift(3)) / nifty_sig["atr"].clip(lower=1e-10)

    # Build events
    events = {}
    for sym, sig in signals_by_symbol.items():
        for i in range(len(sig)):
            events.setdefault(sig.index[i], []).append((sym, i))

    # State
    open_positions: dict[str, v3.OpenPosition] = {}
    trades = []
    equity_rows = []
    last_price: dict[str, float] = {}
    realized_pnl = 0.0
    trade_counter = 0
    peak_equity = p.initial_capital
    current_day = None
    day_start_equity = p.initial_capital
    daily_trade_count = 0
    daily_loss_hit = False
    active_mode = 2  # default to mean reversion
    daily_stats = {}

    for ts in sorted(events.keys()):
        ist = to_ist(ts)
        day = ist.strftime("%Y-%m-%d")

        # Day boundary: recompute regime
        if day != current_day:
            current_day = day
            active_mode = _compute_regime(nifty_sig, ts)
            unreal = sum((p.initial_capital + realized_pnl) for _ in open_positions) * 0
            day_start_equity = p.initial_capital + realized_pnl + v3._mark_to_market(open_positions, last_price)
            daily_trade_count = 0
            daily_loss_hit = False
            daily_stats[day] = {"date": day, "starting_equity": day_start_equity, "trades": 0, "wins": 0, "losses": 0, "gross_pnl": 0.0, "cost": 0.0, "net_pnl": 0.0, "min_equity": day_start_equity}

        # Manage positions (same as V3 engine)
        for sym, i in events.get(ts, []):
            sig = signals_by_symbol[sym]
            candle = sig.iloc[i]
            last_price[sym] = float(candle["close"])
            pos = open_positions.get(sym)
            if pos is None:
                continue
            atr_val = float(candle["atr"]) if not pd.isna(candle["atr"]) else pos.atr_at_entry

            _risk_ps = abs(pos.entry_price - pos.initial_stop)
            pos.bars_held += 1
            if pos.direction == "LONG":
                _fav = float(candle["high"]) - pos.entry_price
                _adv = float(candle["low"]) - pos.entry_price
            else:
                _fav = pos.entry_price - float(candle["low"])
                _adv = pos.entry_price - float(candle["high"])
            if _fav > pos.mfe: pos.mfe = _fav
            if _adv < pos.mae: pos.mae = _adv
            pos.mfe_r = pos.mfe / _risk_ps if _risk_ps > 0 else 0.0
            pos.mae_r = pos.mae / _risk_ps if _risk_ps > 0 else 0.0
            if _risk_ps > 0:
                for lvl in (0.25, 0.50, 0.75, 1.00, 1.50):
                    if _fav / _risk_ps >= lvl and lvl not in pos.ttp:
                        pos.ttp[lvl] = float(pos.bars_held)

            exit_res = v3._check_exit(pos, candle, atr_val, p.intrabar_exit_policy, p.use_target)
            if exit_res is None and ist.strftime("%H:%M") >= "15:15":
                exit_res = ("EOD_SQUARE_OFF", float(candle["close"]))
            if exit_res is not None:
                reason, exit_px = exit_res
                unreal = v3._mark_to_market(open_positions, last_price)
                equity_now = p.initial_capital + realized_pnl + unreal
                trade, realized_pnl = v3._close_position(pos, ts, exit_px, reason, realized_pnl, equity_now, p, trade_counter)
                trade_counter += 1
                trades.append(trade)
                del open_positions[sym]
                ds = daily_stats[day]
                ds["trades"] += 1; ds["gross_pnl"] += trade.gross_pnl; ds["cost"] += trade.estimated_cost; ds["net_pnl"] += trade.net_pnl
                if trade.net_pnl > 0: ds["wins"] += 1
                elif trade.net_pnl < 0: ds["losses"] += 1
                continue
            v3._update_trail(pos, candle, atr_val, p)

        # Portfolio equity / daily loss
        unreal = v3._mark_to_market(open_positions, last_price)
        total_equity = p.initial_capital + realized_pnl + unreal
        daily_pnl = total_equity - day_start_equity
        if day in daily_stats:
            daily_stats[day]["min_equity"] = min(daily_stats[day]["min_equity"], total_equity)
        if p.use_daily_loss_limit and not daily_loss_hit and daily_pnl <= -(day_start_equity * p.max_daily_loss_pct / 100.0):
            daily_loss_hit = True
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                px = last_price.get(sym, pos.entry_price)
                unreal = v3._mark_to_market(open_positions, last_price)
                equity_now = p.initial_capital + realized_pnl + unreal
                trade, realized_pnl = v3._close_position(pos, ts, px, "DAILY_LOSS_LIMIT", realized_pnl, equity_now, p, trade_counter)
                trade_counter += 1; trades.append(trade); del open_positions[sym]
                ds = daily_stats[day]
                ds["trades"] += 1; ds["gross_pnl"] += trade.gross_pnl; ds["cost"] += trade.estimated_cost; ds["net_pnl"] += trade.net_pnl
                if trade.net_pnl > 0: ds["wins"] += 1
                elif trade.net_pnl < 0: ds["losses"] += 1

        # Entry candidates (mode-dependent signals)
        candidates = []
        block_entries = daily_loss_hit or ist.strftime("%H:%M") >= "15:00"
        if not block_entries and daily_trade_count < p.max_trades_per_day and len(open_positions) < p.max_open_positions:
            for sym, i in events.get(ts, []):
                if i == 0 or sym in open_positions:
                    continue
                if not in_allowed_session(ts):
                    continue
                sig = signals_by_symbol[sym]
                prev = sig.iloc[i - 1]
                candle = sig.iloc[i]
                if pd.isna(prev.get("close", np.nan)):
                    continue
                entry_price = apply_slippage(float(candle["open"]), "LONG", True, p.slippage_pct)
                if pd.isna(entry_price):
                    continue
                direction = None
                nifty_bull_n, nifty_px, nifty_ema = nifty_at_candle(nifty_sig, prev.name)

                if active_mode == 1:
                    # Momentum: struct + slope + volume
                    mom_long = bool(prev["close"] > prev["ema20"]) if "ema20" in prev and not pd.isna(prev["ema20"]) else False
                    struct = bool(prev.get("bull_struct", False))
                    slope = bool(prev["ema20_slope"] > 0) if "ema20_slope" in prev and not pd.isna(prev["ema20_slope"]) else False
                    vol = bool(prev["rel_volume"] >= 1.5) if "rel_volume" in prev and not pd.isna(prev["rel_volume"]) else False
                    mom_short = bool(prev["close"] < prev["ema20"]) if "ema20" in prev and not pd.isna(prev["ema20"]) else False
                    struct_s = bool(prev.get("bear_struct", False))
                    slope_s = bool(prev["ema20_slope"] < 0) if "ema20_slope" in prev and not pd.isna(prev["ema20_slope"]) else False
                    if struct and slope and vol and nifty_bull_n is not False: direction = "LONG"
                    elif struct_s and slope_s and vol and nifty_bull_n is not True: direction = "SHORT"
                elif active_mode == 2:
                    # Mean reversion: fade extreme 20-bar return
                    ret20 = prev.get("ret20_pct", 0)
                    if not pd.isna(ret20):
                        thr = prev["close"] * 0.015 if False else ret20  # use quantile approach per-symbol
                        # For simplicity, use a fixed threshold: -1.5% in 20 bars
                        if ret20 < -0.015 and nifty_bull_n is not False: direction = "LONG"
                        elif ret20 > 0.015 and nifty_bull_n is not True: direction = "SHORT"

                if direction is None:
                    continue

                # Build position
                atr_e = float(prev["atr"]) if not pd.isna(prev["atr"]) else 0.0
                rl = float(prev["range_low"]) if not pd.isna(prev.get("range_low", np.nan)) else 0.0
                rh = float(prev["range_high"]) if not pd.isna(prev.get("range_high", np.nan)) else 0.0

                if direction == "LONG":
                    stop = rl if rl > 0 else entry_price - atr_e * 0.3
                else:
                    stop = rh if rh > 0 else entry_price + atr_e * 0.3

                risk_per_s = abs(entry_price - stop)
                if risk_per_s <= 0:
                    continue
                equity_now = p.initial_capital + realized_pnl + v3._mark_to_market(open_positions, last_price)
                risk_amount = equity_now * p.risk_per_trade_pct / 100.0
                qty = max(1, int(np.floor(risk_amount / risk_per_s)))

                max_pos_val = equity_now * p.max_position_value_pct / 100.0
                if entry_price > 0:
                    qty = min(qty, int(np.floor(max_pos_val / entry_price)))
                if qty <= 0:
                    continue
                new_risk = v3._open_risk(open_positions) + risk_per_s * qty
                if new_risk > equity_now * p.max_portfolio_open_risk_pct / 100.0:
                    continue
                target = entry_price + risk_per_s * p.target_rr if direction == "LONG" else entry_price - risk_per_s * p.target_rr

                daily_trade_count += 1
                rw = rh - rl if rh > 0 and rl > 0 else 0.0
                rpct = (rw / max(rl, 1e-10)) * 100 if rl > 0 else 0.0
                vwap_v = float(prev["vwap"]) if not pd.isna(prev.get("vwap", np.nan)) else 0.0
                relv = float(prev["rel_volume"]) if not pd.isna(prev.get("rel_volume", np.nan)) else 0.0
                pos = v3.OpenPosition(
                    symbol=sym, direction=direction, entry_time=ts, entry_price=entry_price,
                    quantity=qty, initial_stop=stop, target=target, trail_price=None,
                    atr_at_entry=atr_e, range_high=rh, range_low=rl, range_width=rw,
                    range_width_pct=rpct, relative_volume=relv, vwap_at_entry=vwap_v,
                    vwap_distance=((entry_price - vwap_v) / vwap_v * 100) if vwap_v else 0.0,
                    nifty_price=nifty_px, nifty_ema=nifty_ema,
                    nifty_regime="BULLISH" if nifty_bull_n is True else "BEARISH" if nifty_bull_n is False else "NEUTRAL",
                    session="S1" if in_allowed_session(ts) and "09:20" <= to_ist(ts).strftime("%H:%M") < "11:00" else "S2",
                    entry_mode=p.entry_mode, signal_score=0.0, equity_before=equity_now,
                    daily_trade_number=daily_trade_count, open_positions_at_entry=len(open_positions),
                    portfolio_open_risk=new_risk,
                )
                open_positions[sym] = pos
                break  # one entry per timestamp (5-trade limit handled by daily_trade_count)

        # Equity curve
        unreal = v3._mark_to_market(open_positions, last_price)
        total_equity = p.initial_capital + realized_pnl + unreal
        peak_equity = max(peak_equity, total_equity)
        dd = peak_equity - total_equity
        dd_pct = (dd / peak_equity * 100) if peak_equity > 0 else 0.0
        equity_rows.append({"timestamp": ts, "realized_pnl": realized_pnl, "unrealized_pnl": unreal, "total_equity": total_equity, "daily_pnl": total_equity - day_start_equity, "drawdown_pct": dd_pct, "open_positions": len(open_positions), "daily_trade_count": daily_trade_count, "active_mode": active_mode})

    # Final close
    if open_positions:
        final_ts = max(events.keys())
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]; px = last_price.get(sym, pos.entry_price)
            equity_now = p.initial_capital + realized_pnl + v3._mark_to_market(open_positions, last_price)
            trade, realized_pnl = v3._close_position(pos, final_ts, px, "EOD_SQUARE_OFF", realized_pnl, equity_now, p, trade_counter)
            trade_counter += 1; trades.append(trade); del open_positions[sym]

    return trades, pd.DataFrame(equity_rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-09-11")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"): print("ERROR: OPENALGO_API_KEY"); sys.exit(1)

    symbols = load_symbols()[:args.sample]
    raw, nifty_raw, quality = load_universe_data(symbols, args.cache, args.start, args.end)
    print_data_quality(quality)
    if not raw: sys.exit(1)

    p = BacktestParams()
    p.estimated_cost_pct = 0.0
    p.slippage_pct = 0.005
    p.max_trades_per_day = 5
    p.max_open_positions = 5
    p.max_portfolio_open_risk_pct = 1.0
    p.use_daily_loss_limit = True
    p.max_daily_loss_pct = 2.0
    p.risk_per_trade_pct = 0.25
    p.use_target = True
    p.target_rr = 1.0
    p.use_trailing = False
    p.intrabar_exit_policy = "CONSERVATIVE"

    trades, equity_df = run_regime_backtest(raw, nifty_raw, p)

    print(f"\nTotal trades: {len(trades)}")
    if trades:
        tdf = pd.DataFrame([t.to_row() for t in trades])
        total_net = tdf["net_pnl"].sum()
        total_gross = tdf["gross_pnl"].sum()
        total_cost = tdf["estimated_cost"].sum()
        wr = (tdf["net_pnl"] > 0).mean() * 100
        wins = tdf[tdf["net_pnl"] > 0]; losses = tdf[tdf["net_pnl"] < 0]
        pf = abs(wins["net_pnl"].sum() / losses["net_pnl"].sum()) if not losses.empty and losses["net_pnl"].sum() != 0 else float("inf") if len(wins) else 0
        print(f"Gross: {total_gross:.0f}  Cost: {total_cost:.0f}  Net: {total_net:.0f}")
        print(f"Win rate: {wr:.1f}%  PF: {pf:.3f}")
        print(f"Final equity: {p.initial_capital+total_net:.0f}")
        print(f"\nExit reasons:")
        print(tdf["exit_reason"].value_counts().to_string())
        print(f"\nLong: n={(tdf['direction']=='LONG').sum()} net={tdf[tdf['direction']=='LONG']['net_pnl'].sum():.0f}")
        print(f"Short: n={(tdf['direction']=='SHORT').sum()} net={tdf[tdf['direction']=='SHORT']['net_pnl'].sum():.0f}")

        # Per-mode analysis
        if "active_mode" not in equity_df.columns:
            pass
        else:
            # Map trades to modes (approximate by entry_time)
            tdf["ts_dt"] = pd.to_datetime(tdf["entry_time"])
            for eq_idx, eq_row in equity_df.iterrows():
                eq_ts = eq_row["timestamp"]
                # This is complex -- skip for now

        tdf.to_csv(f"{OUT_DIR}/regime_v2_trades.csv", index=False)

    if not equity_df.empty:
        equity_df.to_csv(f"{OUT_DIR}/regime_v2_equity.csv", index=False)
        # Mode distribution
        mode_counts = equity_df.groupby("active_mode").size()
        print(f"\nMode distribution (bars):")
        for mode, cnt in mode_counts.items():
            print(f"  Mode {mode}: {cnt} bars")

    print(f"\nAll outputs in {OUT_DIR}/")

if __name__ == "__main__":
    main()