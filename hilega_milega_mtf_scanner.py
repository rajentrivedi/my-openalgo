#!/usr/bin/env python3
"""
Hilega-Milega Pro Multi-Timeframe Scanner

Runs the Hilega-Milega near-bullish-cross detection across four timeframes:
  Weekly (1W), Daily (1D), 1 Hour (1H), 15 Minutes (15m)

Each timeframe is scored 0-100 for proximity to a bullish crossover:
  EMA(3) of RSI(9) crossing above WMA(21) of RSI(9)

Per-timeframe scores are combined (1W 20%, 1D 40%, 1H 25%, 15m 15%) into an
MTF score and consensus label to rank stocks whose bullish setups align across
multiple timeframes.

Usage:
  python scripts/hilega_milega_mtf_scanner.py
  python scripts/hilega_milega_mtf_scanner.py --symbols RELIANCE,TCS,HAL
  python scripts/hilega_milega_mtf_scanner.py --min-score 50 --top 25
  python scripts/hilega_milega_mtf_scanner.py --json
"""

import argparse
import json
import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)

from hilega_milega_scanner import DEFAULT_SYMBOLS, fetch_nifty500_symbols, scan_symbol  # noqa: E402
from hm_confluence import STRATEGY_ORDER, compute_confluence  # noqa: E402

YF_SUFFIX = ".NS"

TF_ORDER = ["1W", "1D", "1H", "15M"]
TF_WEIGHTS = {"1W": 0.20, "1D": 0.40, "1H": 0.25, "15M": 0.15}
TF_CONFIG = {
    "1W": {"interval": "1wk", "period": "3y"},
    "1D": {"interval": "1d", "period": "2y"},
    "1H": {"interval": "1h", "period": "730d"},
    "15M": {"interval": "15m", "period": "60d"},
}
MIN_BARS = {"1W": 30, "1D": 40, "1H": 60, "15M": 60}

XLSX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "hilega_milega_mtf_scanner.xlsx"
)

STATUS_LABEL = {
    "FULL": "FULL",
    "NEAR": "NEAR",
    "CROSSED": "CROSS",
    "FAR": "\u2014",
    "NO_DATA": "n/a",
}


@dataclass
class TimeframeResult:
    tf: str
    status: str
    score: float
    rsi: float
    ema3_rsi: float
    wma21_rsi: float
    cross_distance: float
    rvol: float
    close: float
    prob_1: float
    prob_3: float
    prob_5: float
    failed_filters: list[str] = field(default_factory=list)
    # confluence layer
    confluence: float = 0.0
    quality: float = 0.0
    combo: float = 0.0
    days_since_cross: int = -1
    quality_flags: list[str] = field(default_factory=list)
    cf_tags: str = "-"
    n_bullish: int = 0

    @property
    def freshness(self) -> str:
        d = self.days_since_cross
        if d == -1:
            return "NEAR"
        if d <= 1:
            return "FRESH"
        if d <= 3:
            return "RECENT"
        if d <= 5:
            return "OLD"
        return "STALE"


@dataclass
class MTFScanResult:
    symbol: str
    mtf_score: float
    mtf_class: str
    consensus: str
    consensus_ratio: float
    conflict: bool
    available: int
    aligned: int
    timeframes: dict[str, TimeframeResult] = field(default_factory=dict)
    # confluence layer (daily-anchored)
    confluence: float = 0.0
    quality: float = 0.0
    combo: float = 0.0
    fresh_tfs: int = 0
    stale_tfs: int = 0
    cf_tags: str = "-"

    @property
    def freshness_summary(self) -> str:
        parts = []
        for tf in TF_ORDER:
            tr = self.timeframes.get(tf)
            if tr is not None and tr.status != "NO_DATA" and tr.status != "FAR":
                parts.append(f"{tf}:{tr.freshness}")
        return " ".join(parts) if parts else "-"


def fetch_stock_data(symbol: str, tf: str) -> pd.DataFrame | None:
    cfg = TF_CONFIG[tf]
    yf_symbol = f"{symbol}{YF_SUFFIX}"
    try:
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period=cfg["period"], interval=cfg["interval"])
        if hist.empty:
            return None
        df = pd.DataFrame({
            "date": hist.index,
            "open": hist["Open"].values,
            "high": hist["High"].values,
            "low": hist["Low"].values,
            "close": hist["Close"].values,
            "volume": hist["Volume"].values,
        })
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return None


def empty_timeframe_result(tf: str, status: str) -> TimeframeResult:
    return TimeframeResult(
        tf=tf, status=status, score=0.0, rsi=0.0, ema3_rsi=0.0, wma21_rsi=0.0,
        cross_distance=0.0, rvol=0.0, close=0.0, prob_1=0.0, prob_3=0.0, prob_5=0.0,
    )


def scan_timeframe(symbol: str, tf: str, df: pd.DataFrame | None) -> TimeframeResult:
    if df is None or len(df) < MIN_BARS[tf]:
        return empty_timeframe_result(tf, "NO_DATA")

    result = scan_symbol(symbol, df)
    if result is None:
        return empty_timeframe_result(tf, "FAR")

    if result.cross_distance <= 0:
        status = "CROSSED"
    elif result.is_full_match:
        status = "FULL"
    else:
        status = "NEAR"

    return TimeframeResult(
        tf=tf,
        status=status,
        score=result.score,
        rsi=result.rsi,
        ema3_rsi=result.ema3_rsi,
        wma21_rsi=result.wma21_rsi,
        cross_distance=result.cross_distance,
        rvol=result.rvol,
        close=result.close,
        prob_1=result.prob_1,
        prob_3=result.prob_3,
        prob_5=result.prob_5,
        failed_filters=list(result.failed_filters),
        confluence=result.confluence,
        quality=result.quality,
        combo=result.combo,
        days_since_cross=result.days_since_cross,
        quality_flags=list(result.quality_flags),
        cf_tags=result.cf_tags,
        n_bullish=result.n_bullish,
    )


def scan_symbol_all(symbol: str) -> tuple[str, dict[str, TimeframeResult]]:
    tf_results: dict[str, TimeframeResult] = {}
    for tf in TF_ORDER:
        df = fetch_stock_data(symbol, tf)
        tf_results[tf] = scan_timeframe(symbol, tf, df)
    return symbol, tf_results


def classify_mtf(score: float) -> str:
    if score >= 80:
        return "STRONG"
    if score >= 65:
        return "GOOD"
    if score >= 50:
        return "WATCH"
    return "IGNORE"


def consensus_label(ratio: float) -> str:
    if ratio >= 0.75:
        return "STRONG"
    if ratio >= 0.5:
        return "MIXED"
    return "WEAK"


def combine_timeframes(symbol: str, tf_results: dict[str, TimeframeResult]) -> MTFScanResult | None:
    available = [(tf, tf_results[tf]) for tf in TF_ORDER if tf_results[tf].status != "NO_DATA"]
    if not available:
        return None

    total_w = sum(TF_WEIGHTS[tf] for tf, _ in available)
    mtf_score = round(sum(TF_WEIGHTS[tf] * tr.score for tf, tr in available) / total_w, 1)
    aligned = [tf for tf, tr in available if tr.status in ("FULL", "NEAR", "CROSSED")]
    ratio = round(len(aligned) / len(available), 2)

    d = tf_results.get("1D")
    h = tf_results.get("1H")
    conflict = False
    if (
        d is not None and h is not None
        and d.status != "NO_DATA" and h.status != "NO_DATA"
    ):
        d_ok = d.status in ("FULL", "NEAR", "CROSSED")
        h_ok = h.status in ("FULL", "NEAR", "CROSSED")
        conflict = d_ok != h_ok

    # confluence layer: daily-anchored blend + freshness tally
    d = tf_results.get("1D")
    d_cf = d.confluence if (d is not None and d.status != "NO_DATA") else 0.0
    d_qf = d.quality if (d is not None and d.status != "NO_DATA") else 0.0
    d_tag = d.cf_tags if (d is not None and d.status != "NO_DATA") else "-"

    # cross-TF freshness tally (active TFs only)
    fresh_tfs = 0
    stale_tfs = 0
    for tf in TF_ORDER:
        tr = tf_results.get(tf)
        if tr is None or tr.status in ("NO_DATA", "FAR"):
            continue
        if tr.freshness in ("FRESH", "RECENT", "NEAR"):
            fresh_tfs += 1
        else:
            stale_tfs += 1

    combo = round(0.6 * mtf_score + 0.2 * d_cf + 0.2 * d_qf, 1)

    return MTFScanResult(
        symbol=symbol,
        mtf_score=mtf_score,
        mtf_class=classify_mtf(mtf_score),
        consensus=consensus_label(ratio),
        consensus_ratio=ratio,
        conflict=conflict,
        available=len(available),
        aligned=len(aligned),
        timeframes=tf_results,
        confluence=d_cf,
        quality=d_qf,
        combo=combo,
        fresh_tfs=fresh_tfs,
        stale_tfs=stale_tfs,
        cf_tags=d_tag,
    )


def tf_cell(tr: TimeframeResult | None) -> str:
    if tr is None or tr.status == "NO_DATA":
        return "n/a"
    if tr.status == "FAR":
        return STATUS_LABEL["FAR"]
    return f"{STATUS_LABEL[tr.status]}({tr.score:.0f})"


def print_summary(results: list[MTFScanResult], top_n: int) -> None:
    sep = "\u2500" * 110
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'=' * 110}")
    print("  MULTI-TIMEFRAME HILEGA-MILEGA PRO \u2014 Near Bullish Cross Scanner")
    print("  Timeframes: 1W / 1D / 1H / 15m   Weights: 1W 20% | 1D 40% | 1H 25% | 15m 15%")
    print("  Combo = 60% MTF + 20% D1-confluence + 20% D1-quality")
    print(f"  Generated: {now}")
    print(f"{'=' * 110}")

    if not results:
        print("\n  No candidates above the minimum score threshold.")
        return

    header = (
        f"  {'Rank':>4} {'Symbol':<11} {'Combo':>7} {'MTF':>6} {'CF':>5} {'QF':>5} "
        f"{'Class':<7} {'Fresh':<7} {'Consensus':<9} "
        f"{'1W':>14} {'1D':>14} {'1H':>14} {'15m':>14}"
    )
    print(f"\n  Top {min(top_n, len(results))} Candidates  (ranked by Combo)")
    print(header)
    print(sep)
    for i, r in enumerate(results[:top_n], 1):
        cfl = "YES" if r.conflict else "no"
        cells = [tf_cell(r.timeframes.get(tf)) for tf in TF_ORDER]
        fresh_str = f"{r.fresh_tfs}F/{r.stale_tfs}S"
        print(
            f"  {i:>4} {r.symbol:<11} {r.combo:>7.1f} {r.mtf_score:>6.1f} "
            f"{r.confluence:>5.0f} {r.quality:>5.0f} {r.mtf_class:<7} "
            f"{fresh_str:<7} {r.consensus:<9} "
            f"{cells[0]:>14} {cells[1]:>14} {cells[2]:>14} {cells[3]:>14}"
        )
    print(sep)

    counts = {tf: {"FULL": 0, "NEAR": 0, "CROSSED": 0, "FAR": 0, "NO_DATA": 0} for tf in TF_ORDER}
    for r in results:
        for tf in TF_ORDER:
            tr = r.timeframes.get(tf)
            if tr is not None:
                counts[tf][tr.status] += 1
    print("\n  Per-timeframe tally (within shown candidates):")
    print(f"  {'TF':<6}{'FULL':>6}{'NEAR':>6}{'CROSSED':>8}{'FAR':>6}{'NO_DATA':>8}")
    for tf in TF_ORDER:
        c = counts[tf]
        print(
            f"  {tf:<6}{c['FULL']:>6}{c['NEAR']:>6}{c['CROSSED']:>8}"
            f"{c['FAR']:>6}{c['NO_DATA']:>8}"
        )

    print("\n  Classification:")
    print("    MTF 80+ = STRONG | 65-79 = GOOD | 50-64 = WATCH")
    print("    Status per TF: FULL (all filters) | NEAR (1-3 filters) | CROSS (cross complete) | — (far)")


def print_details(results: list[MTFScanResult], top_n: int) -> None:
    sep = "\u2500" * 110
    for rank, r in enumerate(results[:top_n], 1):
        conflict_tag = " \u26a0 CONFLICT: 1D/1H disagree" if r.conflict else " aligned"
        print(f"\n{sep}")
        print(
            f"  #{rank} {r.symbol}  |  MTF {r.mtf_score}/100  |  {r.mtf_class}  |  "
            f"Consensus {r.consensus} ({r.aligned}/{r.available} TFs){conflict_tag}"
        )
        print(sep)
        for tf in TF_ORDER:
            tr = r.timeframes.get(tf)
            if tr is None:
                continue
            if tr.status == "NO_DATA":
                print(f"  {tf:<4}: n/a")
                continue
            if tr.status == "FAR":
                print(f"  {tf:<4}: \u2014  (far from bullish cross)")
                continue
            prob_str = f"{tr.prob_1:.0f}/{tr.prob_3:.0f}/{tr.prob_5:.0f}%"
            fails = " | ".join(tr.failed_filters) if tr.failed_filters else "\u2014"
            flags = ",".join(tr.quality_flags) if tr.quality_flags else "\u2014"
            print(
                f"  {tf:<4}: {STATUS_LABEL[tr.status]:<6} score {tr.score:>5.1f}  "
                f"RSI {tr.rsi:>5.1f}  Dist {tr.cross_distance:>6.2f}  RVOL {tr.rvol:>4.1f}  "
                f"Prob1/3/5: {prob_str:<9} Fail: {fails}"
            )
            print(
                f"       CF {tr.confluence:>5.1f} | QF {tr.quality:>5.1f} | Combo {tr.combo:>5.1f} "
                f"| {tr.freshness:<6} (dsc={tr.days_since_cross}) | strats {tr.cf_tags:<20} "
                f"| flags {flags}"
            )


def save_xlsx(results: list[MTFScanResult], top_n: int) -> str:
    rows = []
    for rank, r in enumerate(results[:top_n], 1):
        row = {
            "Rank": rank,
            "Symbol": r.symbol,
            "Combo Score": r.combo,
            "MTF Score": r.mtf_score,
            "Confluence": r.confluence,
            "Quality": r.quality,
            "Class": r.mtf_class,
            "Consensus": r.consensus,
            "Conflict": r.conflict,
            "Aligned TFs": f"{r.aligned}/{r.available}",
            "Fresh/Stale": f"{r.fresh_tfs}F/{r.stale_tfs}S",
            "D1 Strats": r.cf_tags,
        }
        for tf in TF_ORDER:
            tr = r.timeframes.get(tf)
            if tr is None:
                continue
            row[f"{tf} Status"] = STATUS_LABEL.get(tr.status, tr.status)
            row[f"{tf} Score"] = round(tr.score, 1)
            row[f"{tf} RSI"] = round(tr.rsi, 1)
            row[f"{tf} Dist"] = round(tr.cross_distance, 2)
            row[f"{tf} RVOL"] = round(tr.rvol, 2)
        rows.append(row)

    df = pd.DataFrame(rows)
    sheet = f"mtf_{datetime.now().strftime('%Y-%m-%d')}"
    try:
        with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet[:31], index=False)
        return f"  Results saved \u2192 {os.path.basename(XLSX_PATH)} [{sheet[:31]}]"
    except Exception as e:
        return f"  [warning] Could not save xlsx: {e}"


def to_json(results: list[MTFScanResult]) -> str:
    return json.dumps(
        [
            {
                "symbol": r.symbol,
                "mtf_score": r.mtf_score,
                "confluence": r.confluence,
                "quality": r.quality,
                "combo": r.combo,
                "fresh_tfs": r.fresh_tfs,
                "stale_tfs": r.stale_tfs,
                "classification": r.mtf_class,
                "consensus": r.consensus,
                "consensus_ratio": r.consensus_ratio,
                "conflict": r.conflict,
                "available_tfs": r.available,
                "aligned_tfs": r.aligned,
                "timeframes": {
                    tf: {
                        "status": tr.status,
                        "score": tr.score,
                        "rsi": tr.rsi,
                        "ema3_rsi": tr.ema3_rsi,
                        "wma21_rsi": tr.wma21_rsi,
                        "cross_distance": tr.cross_distance,
                        "rvol": tr.rvol,
                        "close": tr.close,
                        "prob_1_candle": tr.prob_1,
                        "prob_3_candle": tr.prob_3,
                        "prob_5_candle": tr.prob_5,
                        "failed_filters": tr.failed_filters,
                        "confluence": tr.confluence,
                        "quality": tr.quality,
                        "combo": tr.combo,
                        "days_since_cross": tr.days_since_cross,
                        "freshness": tr.freshness,
                        "quality_flags": tr.quality_flags,
                        "strategy_tags": tr.cf_tags,
                        "bullish_strategies": tr.n_bullish,
                    }
                    for tf, tr in r.timeframes.items()
                },
            }
            for r in results
        ],
        indent=2,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Hilega-Milega Multi-Timeframe Scanner (1W/1D/1H/15m)"
    )
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated symbols to scan (default: Nifty 50 + liquid stocks)",
    )
    parser.add_argument(
        "--min-score", type=float, default=50.0,
        help="Minimum MTF score to display (default: 50)",
    )
    parser.add_argument(
        "--top", type=int, default=25,
        help="Show top N candidates in the summary table (default: 25)",
    )
    parser.add_argument(
        "--detail", type=int, default=10,
        help="Show detailed breakdown for top N candidates (default: 10)",
    )
    parser.add_argument(
        "--threads", type=int, default=8,
        help="Parallel download workers (default: 8)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--nifty500", action="store_true",
        help="Scan all Nifty 500 stocks instead of the default list",
    )
    args = parser.parse_args()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    elif args.nifty500:
        log("Fetching Nifty 500 symbol list from NSE...")
        symbols = fetch_nifty500_symbols()
        log(f"  Found {len(symbols)} symbols")
    else:
        symbols = DEFAULT_SYMBOLS

    def log(*a, **kw):
        """Progress goes to stderr in JSON mode so stdout stays pure JSON."""
        if args.json:
            print(*a, **kw, file=sys.stderr)
        else:
            print(*a, **kw)

    log(f"Scanning {len(symbols)} symbols across 1W / 1D / 1H / 15m...")
    results: list[MTFScanResult] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        for symbol, tf_results in ex.map(scan_symbol_all, symbols):
            combined = combine_timeframes(symbol, tf_results)
            if combined is None:
                errors += 1
                log(f"  {symbol:<12} no data")
                continue
            results.append(combined)
            log(
                f"  {symbol:<12} MTF {combined.mtf_score:>5.1f}  "
                f"{combined.mtf_class:<7}  consensus {combined.consensus}"
            )

    results.sort(key=lambda r: r.combo, reverse=True)
    shown = [r for r in results if r.combo >= args.min_score]

    if args.json:
        print(to_json(shown))
    else:
        print_summary(shown, args.top)
        print_details(shown, args.detail)
        print(f"\n  Summary: {len(shown)} candidates >= {args.min_score} (Combo) | {errors} no-data/errors")
        print(save_xlsx(shown, args.top))
        print(f"\n  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
