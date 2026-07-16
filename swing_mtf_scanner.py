#!/usr/bin/env python3
"""Multi-timeframe swing scanner — daily scan + 1H + 15m analysis for top picks."""

import argparse
import asyncio
import sys
import os
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import asyncpg
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.services.intraday_analyzer import analyze_mtf

# Import scan_daily from sibling module
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
from swing_screener_db import scan_daily  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--kronos", "-k", action="store_true",
                    help="Run Kronos forecast on top picks and append thesis")
parser.add_argument("--kronos-size", default="mini", choices=["mini", "small", "base"])
parser.add_argument("--kronos-top", type=int, default=5,
                    help="How many top picks to forecast (default 5)")
parser.add_argument("--kronos-pred-len", type=int, default=20,
                    help="Forecast horizon in sessions (default 20)")
ARGS = parser.parse_args()

DB_URL = "postgresql://trading:trading@localhost:5436/trading"
TOP_CANDIDATES = 25
OUTPUT_TOP = 10


@dataclass
class MTFResult:
    symbol: str
    sector: str
    close: float
    daily_score: float
    one_h_score: float | None
    fifteen_m_score: float | None
    mtf_score: float
    rsi_9: float
    ema20: float
    ema50: float
    adx: float
    cmf: float
    rvol: float
    atr_14: float
    bias: str
    consensus: str
    has_conflict: bool


XLSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swing_mtf_scanner.xlsx")


def _save_to_xlsx(results: list[MTFResult], top_n: int) -> None:
    sheet_name = date.today().isoformat()
    rows = []
    for r in results[:top_n]:
        rows.append({
            "Symbol": r.symbol,
            "Sector": r.sector,
            "Close": r.close,
            "Daily Score": r.daily_score,
            "1H Score": r.one_h_score or 0,
            "15m Score": r.fifteen_m_score or 0,
            "MTF Score": round(r.mtf_score, 1),
            "RSI(9)": round(r.rsi_9, 1),
            "EMA20": r.ema20,
            "EMA50": r.ema50,
            "ADX": round(r.adx, 1),
            "CMF": round(r.cmf, 4),
            "RVOL": round(r.rvol, 2),
            "ATR-14": round(r.atr_14, 2),
            "Bias": r.bias,
            "Consensus": r.consensus,
            "Conflict": r.has_conflict,
        })

    df = pd.DataFrame(rows)
    sheet_name_trunc = sheet_name[:31]

    if os.path.exists(XLSX_PATH):
        with pd.ExcelWriter(XLSX_PATH, engine="openpyxl", mode="a",
                            if_sheet_exists="replace") as writer:
            if sheet_name_trunc in [s.strip("'") for s in writer.book.sheetnames]:
                del writer.book[sheet_name_trunc]
            df.to_excel(writer, sheet_name=sheet_name_trunc, index=False)
    else:
        with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet_name_trunc, index=False)

    print(f"\n  Results saved → {os.path.basename(XLSX_PATH)} [{sheet_name_trunc}]")


def _run_mtf(symbol: str, daily_score: int) -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = analyze_mtf(symbol, daily_score=daily_score)
        return result
    finally:
        loop.close()


async def main():
    t0 = datetime.now()
    print("=" * 90)
    print("  MULTI-TIMEFRAME SWING SCANNER — Daily + 1H + 15m")
    print("=" * 90)

    conn = await asyncpg.connect(DB_URL)

    daily_results, latest_ti, latest_price_date = await scan_daily(conn, top_n=TOP_CANDIDATES)
    if not daily_results:
        print("\nNo daily scan results. Exiting.")
        await conn.close()
        return

    candidates = daily_results[:TOP_CANDIDATES]
    print(f"\n  Daily candidates: {len(candidates)} (top {TOP_CANDIDATES})")
    print(f"  Running MTF analysis...")

    mtf_results = []
    for i, r in enumerate(candidates):
        result = _run_mtf(r.symbol, r.score)
        one_h = result["timeframes"].get("1h", {}).get("score")
        fifteen_m = result["timeframes"].get("15m", {}).get("score")
        mtf_score = result["mtf_score"]

        mtf_results.append(MTFResult(
            symbol=r.symbol,
            sector=r.sector,
            close=r.close,
            daily_score=r.score,
            one_h_score=one_h,
            fifteen_m_score=fifteen_m,
            mtf_score=mtf_score,
            rsi_9=r.rsi_9,
            ema20=r.ema20,
            ema50=r.ema50,
            adx=r.adx,
            cmf=r.cmf,
            rvol=r.rvol,
            atr_14=r.atr_14,
            bias=r.bias,
            consensus=result["consensus"],
            has_conflict=result["has_conflict"],
        ))
        status = "⚠️" if result["has_conflict"] else "✓"
        print(f"    [{i+1}/{len(candidates)}] {r.symbol:10s}  D:{r.score:3.0f}"
              f"  1H:{f'{one_h:3.0f}' if one_h else ' N/A':>4s}"
              f"  15m:{f'{fifteen_m:3.0f}' if fifteen_m else ' N/A':>4s}"
              f"  MTF:{mtf_score:5.1f}  {status}")

    mtf_results.sort(key=lambda r: r.mtf_score, reverse=True)

    # Reject conflicts: if consensus is bearish, drop to bottom
    mtf_results.sort(key=lambda r: (r.has_conflict, -r.mtf_score))

    await conn.close()

    top = mtf_results[:OUTPUT_TOP]
    elapsed = (datetime.now() - t0).total_seconds()

    print(f"\n{'=' * 90}")
    print(f"  TOP {OUTPUT_TOP} MTF SWING TRADE PICKS  |  "
          f"Scanned {len(daily_results)} daily → {len(candidates)} MTF candidates  |  "
          f"{elapsed:.0f}s")
    print(f"{'=' * 90}")

    for i, r in enumerate(top, 1):
        print(f"\n{'─' * 90}")
        print(f"  #{i}  {r.symbol:10s} | {r.sector:20s} | ₹{r.close:>8.2f}")
        conflict_warn = " ⚠️ SIGNAL CONFLICT" if r.has_conflict else ""
        print(f"      Daily: {r.daily_score:3.0f}  |  1H: {r.one_h_score or 0:3.0f}  |  "
              f"15m: {r.fifteen_m_score or 0:3.0f}  |  "
              f"MTF: {r.mtf_score:5.1f}/100  |  {r.consensus.upper()}{conflict_warn}")
        print(f"{'─' * 90}")

        trend_str = "  ".join(f"{n}: ₹{v:<8.2f} {'▲' if ok else '▼'}" for n, v, ok in [
            ("EMA20", r.ema20, r.close > r.ema20),
            ("EMA50", r.ema50, r.close > r.ema50),
        ])
        print(f"  Trend:   {trend_str}")
        print(f"          EMA20/50: {'▲ BULLISH' if r.ema20 > r.ema50 else '▼ BEARISH'} crossover")

        rsi_s = "Overbought" if r.rsi_9 > 70 else ("Oversold" if r.rsi_9 < 30 else "Neutral")
        print(f"  Momentum: RSI(9): {r.rsi_9:>5.1f} ({rsi_s})  |  ADX: {r.adx:>5.1f}")
        vol_s = "▲ Accumulation" if r.cmf > 0 else "▼ Distribution"
        print(f"  Volume:   RVOL: {r.rvol:>5.2f}x  CMF: {r.cmf:>+8.4f} ({vol_s})")
        print(f"  ATR:      ₹{r.atr_14:.2f} ({r.atr_14 / r.close * 100:.2f}%)")

        atr = r.atr_14
        entry = r.close
        t1 = round(entry + atr * 1.5, 2)
        t2 = round(entry + atr * 3.0, 2)
        sl = round(entry - atr * 1.5, 2)
        rr1 = round((t1 - entry) / (entry - sl), 2) if (entry - sl) > 0 else 0
        print(f"  Trade:    Entry: ₹{entry:>8.2f}  T1: ₹{t1:>8.2f} (+{(t1/entry-1)*100:.1f}%)  "
              f"T2: ₹{t2:>8.2f} (+{(t2/entry-1)*100:.1f}%)")
        print(f"           Stop:  ₹{sl:>8.2f} ({(sl/entry-1)*100:+.1f}%)  R:R 1:{rr1}")
        print(f"  Bias:     {r.bias}  |  MTF Consensus: {r.consensus.upper()}")

        if r.has_conflict:
            print(f"  ⚠️  Signal conflict: intraday timeframes disagree with daily trend. "
                  f"Consider waiting for alignment.")

    _save_to_xlsx(mtf_results, OUTPUT_TOP)

    if ARGS.kronos:
        _kronos_section(top[:ARGS.kronos_top], pred_len=ARGS.kronos_pred_len,
                        size=ARGS.kronos_size)

    print(f"\n{'=' * 90}")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"{'=' * 90}\n")


def _kronos_section(top_picks, pred_len: int = 20, size: str = "mini") -> None:
    from app.services.kronos_service import forecast

    print(f"\n{'=' * 90}")
    print(f"  🤖 KRONOS FORECAST — {size} model, {pred_len}-session horizon, "
          f"top {len(top_picks)} picks")
    print(f"{'=' * 90}")
    print(f"  {'Symbol':<12} {'Close':>9} {'Fcst':>9} {'20d %':>7} "
          f"{'5d %':>7} {'Peak':>9} {'Trough':>9}  Bias")
    print("  " + "-" * 86)
    for r in top_picks:
        try:
            out = forecast(r.symbol, pred_len=pred_len, size=size, sample_count=2)
            bias = ("🟢 UP" if out["ret_20d_pct"] > 1 else
                    "🔴 DOWN" if out["ret_20d_pct"] < -1 else "⚪ FLAT")
            print(f"  {out['symbol']:<12} {out['hist_close']:>9.2f} "
                  f"{out['forecast_last_close']:>9.2f} {out['ret_20d_pct']:>+6.1f}% "
                  f"{out['ret_5d_pct']:>+6.1f}% {out['forecast_peak']:>9.2f} "
                  f"{out['forecast_trough']:>9.2f}  {bias}")
        except Exception as e:
            print(f"  {r.symbol:<12}  Kronos failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
