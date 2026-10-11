"""One-off generator for the shipped universe/candidate data files.

Reproducible, not run at import time. Reads the two validated backtest CSVs
from the sibling ``intraday-strategies/`` repo and emits:

* ``universe.json`` -- the 64 symbols that passed the screen, in rank order.
* ``candidates.csv`` -- the full 494-symbol ranked pool, re-emitted with the
  columns ``Symbol,Trades,TotalR,Win%,PF`` sorted by ``TotalR`` descending.

Run from anywhere::

    python3 strategies/intraday_breakout_bot/data/_gen_universe.py

Kept in the package (rather than deleted) so the data files are auditable and
regenerable. It is not a test module and defines no import-time side effects
beyond the ``__main__`` guard.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

#: data/ -> intraday_breakout_bot -> strategies -> openalgo -> nse
_NSE_ROOT = Path(__file__).resolve().parents[4]
_SOURCE_DIR = _NSE_ROOT / "intraday-strategies"

SCREEN_CSV = _SOURCE_DIR / "SCREEN_TRADES30_R8_WR50_PF15.csv"
N500_CSV = _SOURCE_DIR / "n500_run" / "N500_REPORT.csv"

DATA_DIR = Path(__file__).resolve().parent
UNIVERSE_JSON = DATA_DIR / "universe.json"
CANDIDATES_CSV = DATA_DIR / "candidates.csv"

GENERATED = "2026-10-11"
NOTE = "initial screen from 494-symbol backtest"

#: candidates.csv column order (part of the Task 10 interface).
CANDIDATE_FIELDS = ["Symbol", "Trades", "TotalR", "Win%", "PF"]


def _read_screen_symbols() -> list[str]:
    """The passing symbols, in the screen's rank order."""
    with SCREEN_CSV.open(newline="", encoding="utf-8") as fh:
        return [row["Symbol"] for row in csv.DictReader(fh)]


def _read_n500_rows() -> list[dict]:
    """N500 rows reduced to the candidate columns, sorted by TotalR desc.

    The N500 report's win-rate column is ``Win%`` (not ``WinRatePct`` as in the
    screen file); it is carried through verbatim under the candidate header's
    ``Win%`` column, so no renaming happens here.
    """
    with N500_CSV.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    reduced = [
        {
            "Symbol": r["Symbol"],
            "Trades": r["Trades"],
            "TotalR": r["TotalR"],
            "Win%": r["Win%"],
            "PF": r["PF"],
        }
        for r in rows
    ]
    def _total_r(row: dict) -> float:
        # One N500 row (a 0-trade symbol) has a blank TotalR; it sorts last
        # rather than aborting the whole emission.
        try:
            return float(row["TotalR"])
        except ValueError:
            return float("-inf")

    reduced.sort(key=_total_r, reverse=True)
    return reduced


def main() -> None:
    symbols = _read_screen_symbols()
    if len(symbols) != 64:
        raise SystemExit(
            f"expected 64 screen symbols, got {len(symbols)} from {SCREEN_CSV}"
        )

    UNIVERSE_JSON.write_text(
        json.dumps(
            {"generated": GENERATED, "note": NOTE, "symbols": symbols},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _read_n500_rows()
    with CANDIDATES_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CANDIDATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {UNIVERSE_JSON} ({len(symbols)} symbols)")
    print(f"wrote {CANDIDATES_CSV} ({len(rows)} candidates)")


if __name__ == "__main__":
    main()
