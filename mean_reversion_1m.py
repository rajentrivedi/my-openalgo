"""1-minute mean-reversion signal study + backtest on NSE liquid stocks."""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from dotenv import load_dotenv
load_dotenv(".env", override=True)

from openalgo import api, ta

IST = timezone(timedelta(hours=5, minutes=30))
OUT_DIR = "diagnostics"; os.makedirs(OUT_DIR, exist_ok=True)

def _compute(df, lookback=10):
    data = df.copy()
    for c in ("close","high","low","open","volume"):
        data[c] = pd.to_numeric(data[c], errors="coerce")
    data = data.dropna()
    data["atr"] = ta.atr(data["high"], data["low"], data["close"], 14)
    # momentum returns
    for b in (1,3,5,10,20,30):
        data[f"ret_{b}"] = data["close"].pct_change(b)
    # forward returns
    for b, lbl in ((1,"f1"),(3,"f3"),(6,"f6"),(12,"f12"),(24,"f24"),(60,"f60")):
        data[f"fwd_{lbl}"] = data["close"].shift(-b) / data["close"] - 1
    data["atr_pct"] = data["atr"] / data["close"].clip(lower=1e-10) * 100
    return data

def main():
    api_key = os.getenv("OPENALGO_API_KEY")
    api_host = os.getenv("OPENALGO_HOST","http://127.0.0.1:5000")
    if not api_key: print("ERROR: OPENALGO_API_KEY"); sys.exit(1)

    client = api(api_key=api_key, host=api_host)

    symbols = []
    with open("data/qtyfreeze.csv") as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[1] and parts[1] not in ("SYMBOL","BANKNIFTY","FINNIFTY","MIDCPNIFTY","NIFTY","NIFTYNXT50"):
                symbols.append(parts[1])
                if len(symbols) >= 20: break
    print(f"Universe: {len(symbols)} symbols")

    end = datetime.now(IST).strftime("%Y-%m-%d")
    start = (datetime.now(IST) - timedelta(days=30)).strftime("%Y-%m-%d")

    # Fetch all 1m data
    print(f"Fetching 1m data {start} to {end}...")
    raw = {}
    for idx, sym in enumerate(symbols):
        sys.stdout.write(f"\r  [{idx+1}/{len(symbols)}] {sym}"); sys.stdout.flush()
        try:
            df = client.history(symbol=sym, exchange="NSE", interval="1m", start_date=start, end_date=end)
            if isinstance(df, pd.DataFrame) and not df.empty:
                raw[sym] = df
        except: pass
    print(f"\nLoaded {len(raw)} symbols")

    if not raw: return

    # Signal study
    print("\n=== Signal Study ===")
    all_rows = []
    for sym, df in raw.items():
        d = _compute(df)
        if not d.empty: all_rows.append(d)
    big = pd.concat(all_rows) if all_rows else pd.DataFrame()
    if big.empty: return
    print(f"Total rows: {len(big)}")

    # Check decile edge at 1m
    for look in (5, 10, 20):
        col = f"ret_{look}"
        if col not in big.columns: continue
        thr = big[col].quantile(0.10)
        bottom = big[big[col] < thr]
        top = big[big[col] > big[col].quantile(0.90)]
        mid = big[(big[col] >= thr) & (big[col] <= big[col].quantile(0.90))]
        print(f"\n  ret_{look} bottom 10% (thr={thr:.4f}):")
        for lbl in ("f1","f3","f6","f12","f60"):
            cl = f"fwd_{lbl}"
            if cl not in bottom.columns: continue
            bm = bottom[cl].dropna().mean() * 100
            bw = (bottom[cl].dropna() > 0).mean() * 100
            mm = mid[cl].dropna().mean() * 100 if not mid.empty else 0
            print(f"    fwd_{lbl}: mean={bm:.4f}% wr={bw:.1f}% (mid={mm:.4f}%)")

    # Signal by atr_pct bucket
    print("\n  Bottom decile by ATR%:")
    col = "ret_10"
    thr = big[col].quantile(0.10)
    bottom = big[big[col] < thr].copy()
    bottom["atr_bucket"] = pd.qcut(bottom["atr_pct"].fillna(0.5), q=3, labels=["low ATR","mid ATR","high ATR"])
    for lbl, cl_in in [("f3","fwd_f3"),("f6","fwd_f6")]:
        if cl_in not in bottom.columns: continue
        for ab in ["low ATR","mid ATR","high ATR"]:
            sub = bottom[bottom["atr_bucket"] == ab]
            print(f"    {ab}: n={len(sub)} fwd_{lbl}={sub[cl_in].dropna().mean()*100:.4f}% wr={(sub[cl_in].dropna()>0).mean()*100:.1f}%")

    # Build and backtest mean-reversion
    print("\n=== Backtest ===")
    dates = sorted(big["timestamp"].unique()) if "timestamp" in big.columns else sorted(big.index.unique())
    
    ticker_live = {}
    for sym, df in raw.items():
        d = _compute(df)
        if not d.empty:
            d["sym"] = sym
            ticker_live[sym] = d

    # Simple bar-by-bar simulation
    initial = 100000.0
    equity = initial
    positions = {}
    trades = []
    equity_curve = []

    col = "ret_10"
    for sym, df in ticker_live.items():
        thr = df[col].quantile(0.10)
        df["signal"] = (df[col] < thr) & (df[col].notna()) & (df[col] < 0)
        df["signal_short"] = (df[col] > df[col].quantile(0.90)) & (df[col].notna()) & (df[col] > 0)
        df = df.dropna(subset=["close", "atr"])
        ticker_live[sym] = df

    # Get all timestamps
    all_ts = sorted({ts for df in ticker_live.values() for ts in df.index})
    trade_id = 0
    daily_trades = 0
    last_day = None

    for ts in all_ts:
        day = str(ts.date())
        if day != last_day:
            daily_trades = 0
            last_day = day

        # Manage positions
        for sym in list(positions.keys()):
            pos = positions[sym]
            if sym not in ticker_live: continue
            if ts not in ticker_live[sym].index: continue
            bar = ticker_live[sym].loc[ts]
            price = float(bar["close"])
            atr = float(bar["atr"])
            target = pos["entry"] + atr * 0.3 if pos["dir"] == "LONG" else pos["entry"] - atr * 0.3
            stop = pos["entry"] - atr * 0.15 if pos["dir"] == "LONG" else pos["entry"] + atr * 0.15
            exit_reason = None
            exit_px = price

            if pos["dir"] == "LONG":
                if price >= target: exit_reason = "TARGET"; exit_px = target
                elif price <= stop: exit_reason = "STOP"; exit_px = stop
            else:
                if price <= target: exit_reason = "TARGET"; exit_px = target
                elif price >= stop: exit_reason = "STOP"; exit_px = stop

            if exit_reason or str(ts)[-8:] >= "15:20":
                if not exit_reason: exit_reason = "EOD"
                gross = (exit_px - pos["entry"]) * pos["qty"] * (1 if pos["dir"] == "LONG" else -1)
                trades.append({"trade_id": trade_id, "sym": sym, "dir": pos["dir"], "entry": pos["entry"],
                               "exit": exit_px, "qty": pos["qty"], "pnl": gross, "reason": exit_reason})
                equity += gross
                trade_id += 1
                del positions[sym]

        # Enter new signals (only if within allowed sessions)
        hour_min = str(ts)[-8:-3]  # HH:MM
        in_s1 = "09:20" <= hour_min <= "11:00"
        in_s2 = "13:30" <= hour_min <= "14:50"
        if not (in_s1 or in_s2): continue
        if daily_trades >= 5: continue
        if len(positions) >= 5: continue

        for sym in ticker_live:
            if sym in positions: continue
            df = ticker_live[sym]
            if ts not in df.index: continue
            bar = df.loc[ts]
            if not bool(bar["signal"]): continue
            entry_price = float(bar["close"])
            atr = float(bar["atr"])
            risk = atr * 0.15
            qty = max(1, int(equity * 0.0025 / risk))
            positions[sym] = {"entry": entry_price, "qty": qty, "dir": "LONG"}
            daily_trades += 1
            break
        else:
            for sym in ticker_live:
                if sym in positions: continue
                df = ticker_live[sym]
                if ts not in df.index: continue
                bar = df.loc[ts]
                if not bool(bar["signal_short"]): continue
                entry_price = float(bar["close"])
                atr = float(bar["atr"])
                risk = atr * 0.15
                qty = max(1, int(equity * 0.0025 / risk))
                positions[sym] = {"entry": entry_price, "qty": qty, "dir": "SHORT"}
                daily_trades += 1
                break

        equity_curve.append({"ts": ts, "equity": equity, "open": len(positions)})

    # Results
    print(f"\n=== Results ({len(trades)} trades) ===")
    if trades:
        tdf = pd.DataFrame(trades)
        gross = tdf["pnl"].sum()
        win = (tdf["pnl"] > 0).sum()
        loss = (tdf["pnl"] < 0).sum()
        print(f"Gross P&L: {gross:.2f}")
        print(f"Win rate: {win/len(tdf)*100:.1f}% ({win}/{len(tdf)})")
        print(f"Exit reasons:")
        print(tdf["reason"].value_counts().to_string())
        print(f"Final equity: {equity:.2f} (return {(equity-initial)/initial*100:.2f}%)")
        tdf.to_csv(f"{OUT_DIR}/mr_1m_trades.csv", index=False)
    if equity_curve:
        pd.DataFrame(equity_curve).to_csv(f"{OUT_DIR}/mr_1m_equity.csv", index=False)

    print(f"\nOutputs in {OUT_DIR}/")

if __name__ == "__main__":
    main()