"""
Regime V3 — Three independent market-regime strategies.
Fixes V2 problems: intraday regime, mutual exclusivity, independent SR exits, proper metadata, realistic costs.
"""
from __future__ import annotations
import argparse, json, os, sys, time
from collections import defaultdict
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
load_dotenv(".env", override=True)

from backtest_range_breakout import (
    IST, BacktestParams, compute_nifty_signals, compute_signals,
    load_symbols, load_universe_data, print_data_quality,
    to_ist, in_allowed_session, nifty_at_candle, apply_slippage,
    ta as _ta,
)
import backtest_range_breakout as v3
from regime_engine import compute_regime, BULL, BEAR, SIDEWAYS, TRANSITION, ALL_REGIMES

OUT_DIR = "diagnostics/regime_v3"
os.makedirs(OUT_DIR, exist_ok=True)

STRATEGY_BULL = "BULL_MOMENTUM"
STRATEGY_BEAR = "BEAR_MOMENTUM"
STRATEGY_SIDEWAYS = "SIDEWAYS_MEAN_REVERSION"
STRATEGY_NONE = "NO_TRADE"


@dataclass
class RegimeTrade:
    trade_id: str; symbol: str; direction: str; strategy: str; regime: str
    signal_ts: Any; entry_ts: Any; entry_price: float
    stop_price: float; target_price: float
    exit_ts: Any = None; exit_price: float = 0.0; exit_reason: str = ""
    quantity: int = 0; gross_pnl: float = 0.0; cost: float = 0.0; slippage_cost: float = 0.0
    net_pnl: float = 0.0; risk_amount: float = 0.0; r_multiple: float = 0.0
    mfe: float = 0.0; mae: float = 0.0; holding_bars: int = 0; holding_minutes: float = 0.0
    nifty_at_entry: float = 0.0

    def to_dict(self) -> dict:
        return {k: (str(v) if hasattr(v, "strftime") else v) for k, v in self.__dict__.items()}


def run_v3(raw, nifty_raw, p: BacktestParams):
    """Complete Regime V3 backtest. Returns (trades, equity_df, regime_df)."""
    # Precompute signals
    sigs = {}
    for sym, df in raw.items():
        sig = compute_signals(df, p)
        sig["ret20"] = sig["close"].pct_change(20)
        sigs[sym] = sig

    # NIFTY setup
    nifty_sig = compute_nifty_signals(nifty_raw, p) if nifty_raw is not None else None
    if nifty_sig is not None:
        nifty_sig["atr"] = _ta.atr(nifty_sig["high"], nifty_sig["low"], nifty_sig["close"], 14)
        nifty_sig["ema20"] = nifty_sig["close"].ewm(span=20, adjust=False).mean()
        nifty_sig["ema50"] = nifty_sig["close"].ewm(span=50, adjust=False).mean()
        nifty_sig["ema20_slope"] = (nifty_sig["ema20"] - nifty_sig["ema20"].shift(3)) / nifty_sig["atr"].clip(lower=1e-10)

    # Events
    events = defaultdict(list)
    for sym, sig in sigs.items():
        for i in range(len(sig)):
            events[sig.index[i]].append((sym, i))

    open_positions: dict[str, RegimeTrade] = {}
    trades: list[RegimeTrade] = []
    equity_rows, regime_rows = [], []
    realized_pnl, trade_counter, peak_equity = 0.0, 0, p.initial_capital
    current_day, daily_trade_count, daily_loss_hit = None, 0, False
    day_start_equity = p.initial_capital
    active_regime = SIDEWAYS

    for ts in sorted(events):
        ist = to_ist(ts); day = ist.strftime("%Y-%m-%d")
        hhmm = ist.strftime("%H:%M")

        # Day/reset
        active_regime = compute_regime(nifty_sig, ts, confirm_bars=1)
        if day != current_day:
            current_day = day
            unreal = v3._mark_to_market({k: _regime_trade_to_open(k, v) for k, v in open_positions.items()}, {k: sigs[k].loc[ts]["close"] if k in sigs and ts in sigs[k].index else v.entry_price for k, v in open_positions.items()})
            day_start_equity = p.initial_capital + realized_pnl + unreal
            daily_trade_count = 0; daily_loss_hit = False

        # Manage positions
        for sym, i in events.get(ts, []):
            if sym not in open_positions: continue
            sig = sigs[sym]; candle = sig.iloc[i]
            pos = open_positions[sym]
            atr_val = float(candle["atr"]) if not pd.isna(candle.get("atr", np.nan)) else p.atr_length
            close = float(candle["close"]); high = float(candle["high"]); low = float(candle["low"])
            exit_reason = None; exit_px = close
            _stop = pos.stop_price; _target = pos.target_price

            # MFE/MAE
            if pos.direction == "LONG":
                pos.mfe = max(pos.mfe, high - pos.entry_price)
                pos.mae = min(pos.mae, low - pos.entry_price)
                adj_stop = _stop + pos.mfe * p.target_rr * 0.5 if pos.strategy != STRATEGY_SIDEWAYS else _stop
                # Check exits
                if low <= _stop: exit_reason = "STOP_LOSS"; exit_px = _stop
                elif high >= _target: exit_reason = "TARGET"; exit_px = _target
            else:
                pos.mfe = max(pos.mfe, pos.entry_price - low)
                pos.mae = max(pos.mae, pos.entry_price - high)
                if high >= _stop: exit_reason = "STOP_LOSS"; exit_px = _stop
                elif low <= _target: exit_reason = "TARGET"; exit_px = _target

            if exit_reason is None and hhmm >= "15:15":
                exit_reason = "EOD_SQUARE_OFF"; exit_px = close

            if exit_reason:
                pos.exit_ts = ts; pos.exit_price = exit_px; pos.exit_reason = exit_reason
                exit_adverse = apply_slippage(exit_px, pos.direction, is_entry=False, slippage_pct=p.slippage_pct)
                if pos.direction == "LONG":
                    gross = (exit_adverse - pos.entry_price) * pos.quantity
                else:
                    gross = (pos.entry_price - exit_adverse) * pos.quantity
                    pos.gross_pnl = gross; pos.slippage_cost = abs(gross - (exit_px - pos.entry_price) * pos.quantity * (1 if pos.direction == "LONG" else -1))
                pos.gross_pnl = gross
                turnover = abs(pos.entry_price * pos.quantity) + abs(exit_adverse * pos.quantity)
                pos.cost = turnover * p.estimated_cost_pct / 100.0
                pos.net_pnl = gross - pos.cost - pos.slippage_cost
                pos.r_multiple = gross / pos.risk_amount if pos.risk_amount != 0 else 0.0
                pos.holding_bars = pos.holding_bars or max(1, int((ts - pos.entry_ts).total_seconds() / 300))
                pos.holding_minutes = (ts - pos.entry_ts).total_seconds() / 60.0 if hasattr(ts, "__sub__") else 0
                realized_pnl += pos.net_pnl
                trades.append(pos); del open_positions[sym]

            pos.holding_bars += 1

        # Daily loss check
        unreal = v3._mark_to_market(
            {k: _regime_trade_to_open(k, v) for k, v in open_positions.items()},
            {k: (sigs[k].loc[ts]["close"] if k in sigs and ts in sigs[k].index else v.entry_price) for k, v in open_positions.items()}
        )
        total_equity = p.initial_capital + realized_pnl + unreal
        daily_pnl = total_equity - day_start_equity
        if p.use_daily_loss_limit and not daily_loss_hit and daily_pnl <= -(day_start_equity * p.max_daily_loss_pct / 100.0):
            daily_loss_hit = True
            for sym in list(open_positions.keys()):
                pos = open_positions.pop(sym); pos.exit_reason = "DAILY_LOSS"
                realized_pnl += pos.net_pnl; trades.append(pos)

        # Entries — only if daily trade count < limit
        if daily_trade_count < p.max_trades_per_day and not daily_loss_hit:
            _candidates = []
            for sym, i in events.get(ts, []):
                if i == 0 or sym in open_positions: continue
                if not in_allowed_session(ts) or hhmm >= "15:00": continue
                sig = sigs[sym]; prev = sig.iloc[i - 1]; candle = sig.iloc[i]
                nifty_bull, nifty_px, _ = nifty_at_candle(nifty_sig, prev.name)
                entry_open = float(candle["open"])
                if pd.isna(entry_open): continue

                entry_data = None
                if active_regime == BULL:
                    entry_data = _bull_signal(prev, nifty_bull, p)
                elif active_regime == BEAR:
                    entry_data = _bear_signal(prev, nifty_bull, p)
                elif active_regime == SIDEWAYS:
                    entry_data = _sideways_signal(prev, candle, p)
                # TRANSITION → no entry

                if entry_data is None: continue
                direction, stop_px, target_px, score = entry_data

                if direction is None: continue
                entry = apply_slippage(entry_open, direction, True, p.slippage_pct)
                risk = abs(entry - stop_px)
                if risk <= 0: continue
                eq_now = p.initial_capital + realized_pnl + v3._mark_to_market({}, {})
                risk_amt = eq_now * p.risk_per_trade_pct / 100.0
                qty = max(1, int(risk_amt / risk))
                max_val = eq_now * p.max_position_value_pct / 100.0
                qty = min(qty, int(max_val / entry) if entry > 0 else qty)

                strat = {BULL: STRATEGY_BULL, BEAR: STRATEGY_BEAR, SIDEWAYS: STRATEGY_SIDEWAYS, TRANSITION: STRATEGY_NONE}[active_regime]
                pos = RegimeTrade(trade_id=f"V3_{trade_counter}", symbol=sym, direction=direction, strategy=strat, regime=active_regime, signal_ts=prev.name, entry_ts=ts, entry_price=entry, stop_price=stop_px, target_price=target_px, quantity=qty, risk_amount=risk * qty, nifty_at_entry=nifty_px)
                open_positions[sym] = pos

            if _candidates:
                _candidates.sort(key=lambda c: c[3], reverse=True)
                taken = min(len(_candidates), p.max_trades_per_day - daily_trade_count, p.max_open_positions - len(open_positions))
                for j in range(taken):
                    sym, _, _, _ = _candidates[j]; # already added above

                daily_trade_count += taken

        # Equity curve
        peak_equity = max(peak_equity, total_equity)
        dd = peak_equity - total_equity; ddp = dd / peak_equity * 100 if peak_equity > 0 else 0
        equity_rows.append({"timestamp": ts, "realized_pnl": realized_pnl, "unrealized_pnl": unreal, "total_equity": total_equity, "drawdown_pct": round(ddp, 4), "open_positions": len(open_positions), "daily_trade_count": daily_trade_count, "regime": active_regime})
        regime_rows.append({"timestamp": ts, "regime": active_regime})

    return trades, pd.DataFrame(equity_rows), pd.DataFrame(regime_rows)


def _regime_trade_to_open(sym, t: RegimeTrade):
    """Convert RegimeTrade to OpenPosition-like dict for mark_to_market."""
    class _P:
        entry_price = t.entry_price; quantity = t.quantity; direction = t.direction
    return _P()


def _bull_signal(prev: pd.Series, nifty_bull_n: bool | None, p: BacktestParams):
    """BULL_MOMENTUM: long-only trend continuation."""
    if nifty_bull_n is False: return None
    close = float(prev["close"])
    ema20 = float(prev.get("ema20", 0)); ema50 = float(prev.get("ema50", 0))
    slope = float(prev.get("ema20_slope", 0)); rvol = float(prev.get("rel_volume", 0))
    atr = float(prev.get("atr", 0.5))
    if pd.isna(ema20) or pd.isna(ema50) or close <= ema20 or ema20 <= ema50 or slope <= 0: return None
    if rvol < 1.5: return None
    stop = close - atr * 0.5; target = close + atr * 1.0
    return "LONG", stop, target, rvol + (close - ema20) / max(atr, 1e-10)


def _bear_signal(prev: pd.Series, nifty_bull_n: bool | None, p: BacktestParams):
    """BEAR_MOMENTUM: short-only trend continuation."""
    if nifty_bull_n is True: return None
    close = float(prev["close"])
    ema20 = float(prev.get("ema20", 0)); ema50 = float(prev.get("ema50", 0))
    slope = float(prev.get("ema20_slope", 0)); rvol = float(prev.get("rel_volume", 0))
    atr = float(prev.get("atr", 0.5))
    if pd.isna(ema20) or pd.isna(ema50) or close >= ema20 or ema20 >= ema50 or slope >= 0: return None
    if rvol < 1.5: return None
    stop = close + atr * 0.5; target = close - atr * 1.0
    return "SHORT", stop, target, rvol + (ema20 - close) / max(atr, 1e-10)


def _sideways_signal(prev: pd.Series, candle: pd.Series, p: BacktestParams):
    """SIDEWAYS_MEAN_REVERSION: fade extreme VWAP/EMA deviation. No breakout concepts."""
    close = float(prev["close"])
    vwap = float(prev.get("vwap", close))
    ema20 = float(prev.get("ema20", close))
    atr = float(prev.get("atr", 0.5))
    rvol = float(prev.get("rel_volume", 0))
    if pd.isna(atr) or atr <= 0: return None
    vwap_dev = (close - vwap) / atr
    ema_dev = (close - ema20) / atr
    # Long when price is significantly below VWAP and EMA20 (oversold)
    if vwap_dev < -1.0 and ema_dev < -0.8 and rvol >= 1.0:
        stop = close - atr * 0.3; target = min(vwap, ema20)
        return "LONG", stop, target, abs(vwap_dev * 100)
    # Short when price is significantly above VWAP and EMA20 (overbought)
    if vwap_dev > 1.0 and ema_dev > 0.8 and rvol >= 1.0:
        stop = close + atr * 0.3; target = max(vwap, ema20)
        return "SHORT", stop, target, abs(vwap_dev * 100)
    return None


def _stats(trades: list[RegimeTrade]):
    if not trades: return {}
    df = pd.DataFrame([t.to_dict() for t in trades])
    for c in ["gross_pnl","cost","net_pnl"]:
        if c not in df.columns: df[c] = 0.0
    return {
        "n": len(df), "gross": float(df["gross_pnl"].sum()), "cost": float(df["cost"].sum()), "net": float(df["net_pnl"].sum()),
        "wr": float((df["net_pnl"]>0).mean()*100) if len(df) else 0,
        "pf": abs(df[df["net_pnl"]>0]["net_pnl"].sum()/df[df["net_pnl"]<0]["net_pnl"].sum()) if (df["net_pnl"]<0).any() and df[df["net_pnl"]<0]["net_pnl"].sum()!=0 else (float("inf") if (df["net_pnl"]>0).any() else 0),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-09-11")
    parser.add_argument("--cost", type=float, default=0.02, help="baseline estimated cost %%")
    parser.add_argument("--slip", type=float, default=0.01, help="baseline slippage %%")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"): print("ERROR: OPENALGO_API_KEY"); sys.exit(1)
    symbols = load_symbols()[:args.sample]
    raw, nifty_raw, quality = load_universe_data(symbols, args.cache, args.start, args.end)
    print_data_quality(quality)
    if not raw: sys.exit(1)

    all_ts = sorted({ts for df in raw.values() for ts in df.index})
    n = len(all_ts)
    train_end = all_ts[int(n*0.50)]; val_end = all_ts[int(n*0.70)]; test_end = all_ts[int(n*0.85)]
    print(f"Train<{train_end} Val<{val_end} Test<{test_end} Fresh>{test_end}")

    def _sub(raw_d, nifty_d, cost, slip):
        p = BacktestParams()
        p.estimated_cost_pct = cost; p.slippage_pct = slip
        p.max_trades_per_day = 5; p.max_open_positions = 5; p.max_portfolio_open_risk_pct = 1.0
        p.use_daily_loss_limit = True; p.max_daily_loss_pct = 2.0; p.risk_per_trade_pct = 0.25
        p.use_target = True; p.use_trailing = False; p.target_rr = 1.0; p.intrabar_exit_policy = "CONSERVATIVE"
        return run_v3(raw_d, nifty_d, p)

    tr = {s:d[d.index<train_end] for s,d in raw.items() if (d.index<train_end).any()}
    va = {s:d[(d.index>=train_end)&(d.index<val_end)] for s,d in raw.items() if ((d.index>=train_end)&(d.index<val_end)).any()}
    te = {s:d[(d.index>=val_end)&(d.index<test_end)] for s,d in raw.items() if ((d.index>=val_end)&(d.index<test_end)).any()}
    fr = {s:d[d.index>=test_end] for s,d in raw.items() if (d.index>=test_end).any()}
    tn = nifty_raw[nifty_raw.index<train_end] if nifty_raw is not None else None
    vn = nifty_raw[(nifty_raw.index>=train_end)&(nifty_raw.index<val_end)] if nifty_raw is not None else None
    ten = nifty_raw[(nifty_raw.index>=val_end)&(nifty_raw.index<test_end)] if nifty_raw is not None else None
    fn = nifty_raw[nifty_raw.index>=test_end] if nifty_raw is not None else None

    # Baseline run
    print(f"\n=== Regime V3 (cost={args.cost}% slip={args.slip}%) ===")
    trades, eq_df, reg_df = _sub(raw, nifty_raw, args.cost, args.slip)
    st = _stats(trades)
    print(f"Total: {st.get('n',0)} trades, net={st.get('net',0):.0f}, wr={st.get('wr',0):.1f}%, pf={st.get('pf',0):.3f}")
    if trades:
        tdf = pd.DataFrame([t.to_dict() for t in trades])
        tdf.to_csv(f"{OUT_DIR}/regime_v3_trades.csv", index=False)
        for s_name in [STRATEGY_BULL, STRATEGY_BEAR, STRATEGY_SIDEWAYS]:
            sub = tdf[tdf["strategy"] == s_name]
            if not sub.empty:
                print(f"  {s_name}: n={len(sub)} net={sub['net_pnl'].sum():.0f} wr={(sub['net_pnl']>0).mean()*100:.1f}%")
        for r_name in ALL_REGIMES:
            sub = tdf[tdf["regime"] == r_name]
            if not sub.empty:
                print(f"  Regime {r_name}: n={len(sub)} net={sub['net_pnl'].sum():.0f} wr={(sub['net_pnl']>0).mean()*100:.1f}%")
    if not eq_df.empty: eq_df.to_csv(f"{OUT_DIR}/regime_v3_equity.csv", index=False)
    if not reg_df.empty: reg_df.to_csv(f"{OUT_DIR}/regime_v3_regimes.csv", index=False)

    # Walk-forward
    print("\n=== Walk-Forward ===")
    wf_rows = []
    for w in range(4):
        wf_tr = all_ts[int(n*0.30*(1+w*0.10))]
        wf_te = all_ts[min(int(n*(0.40+w*0.10)),n-1)]
        wr_ = {s:d[(d.index>=wf_tr)&(d.index<wf_te)] for s,d in raw.items() if ((d.index>=wf_tr)&(d.index<wf_te)).any()}
        wn = nifty_raw[(nifty_raw.index>=wf_tr)&(nifty_raw.index<wf_te)] if nifty_raw is not None else None
        if not wr_: continue
        tw, _, _ = _sub(wr_, wn, args.cost, args.slip)
        sw = _stats(tw)
        wf_rows.append({"window":w+1,"n":sw.get("n",0),"net":round(sw.get("net",0)),"pf":round(sw.get("pf",0),3)})
        print(f"  W{w+1}: n={sw.get('n',0)} net={sw.get('net',0):.0f} pf={sw.get('pf',0):.3f}")
    pd.DataFrame(wf_rows).to_csv(f"{OUT_DIR}/regime_v3_walkforward.csv", index=False)

    # Cost stress
    print("\n=== Cost Stress ===")
    cost_rows = []
    for cost in (0.0, 0.01, 0.02, 0.03, 0.05):
        tw, _, _ = _sub(raw, nifty_raw, cost, args.slip)
        sw = _stats(tw)
        cost_rows.append({"cost_pct":cost, **sw})
        print(f"  cost={cost:.2f}: n={sw.get('n',0)} net={sw.get('net',0):.0f} pf={sw.get('pf',0):.3f}")
    pd.DataFrame(cost_rows).to_csv(f"{OUT_DIR}/regime_v3_cost_stress.csv", index=False)

    # Summary
    print(f"\nOutputs: {OUT_DIR}/")

if __name__ == "__main__":
    main()