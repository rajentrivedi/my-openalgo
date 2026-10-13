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

Task 10 split the two emissions into separate entry points:

* ``write_universe()`` rewrites ``universe.json`` from the static screen.
* ``write_candidates(report_path=...)`` re-emits ``candidates.csv`` from *any*
  batch report, defaulting to the static ``N500_REPORT.csv`` for the standalone
  one-off. The monthly job (:mod:`universe_monthly`) imports this module and
  calls ``write_candidates`` with the *fresh* ``batch_report.csv`` that
  ``run_batch_backtest.py`` just produced, so the monthly rebuild reflects the
  new backtest -- and it never calls ``write_universe``, so a monthly run can
  never reset the live book. ``main`` still writes both files from the static
  source, preserving the standalone behaviour.
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

#: Maps each emitted candidate field to the report columns that may carry it,
#: most-specific first. The static ``N500_REPORT.csv`` uses the capitalised
#: names (``Symbol/Trades/TotalR/Win%/PF``); the fresh ``batch_report.csv`` that
#: ``run_batch_backtest.py`` writes uses lowercase snake_case
#: (``symbol/trades/total_r/win_rate_pct/profit_factor``). Carrying both here
#: lets one reader serve the standalone one-off and the monthly rebuild without
#: duplicating the emission logic.
_FIELD_SOURCE_ALIASES = {
    "Symbol": ("Symbol", "symbol"),
    "Trades": ("Trades", "trades"),
    "TotalR": ("TotalR", "total_r"),
    "Win%": ("Win%", "win_rate_pct"),
    "PF": ("PF", "profit_factor", "pf"),
}


#: OpenAlgo's NSE symbol master, used to undo the CSV export's filename
#: sanitization. ``database/historify_db.py::_sanitize_filename`` rewrites every
#: character outside ``[A-Za-z0-9_\-.]`` to ``_`` when it names an archive member
#: and ships no manifest, so a screen run over those CSVs reports ``J_KBANK``
#: for ``J&KBANK`` and ``M_M`` for ``M&M``. Those names reach the API and come
#: back 400, which silently drops the symbol from the plan.
_SYMBOL_MASTER_DB = _NSE_ROOT / "openalgo-chart" / "db" / "openalgo.db"

#: Fallback when the master is unreadable, covering the sanitized names the
#: exports actually contain.
_SYMBOL_OVERRIDES = {
    "ARE_M": "ARE&M",
    "GVT_D": "GVT&D",
    "J_KBANK": "J&KBANK",
    "M_M": "M&M",
    "M_MFIN": "M&MFIN",
}

_MASTER_CACHE: set[str] = set()


def _nse_master() -> set[str]:
    """Every symbol the OpenAlgo master knows for NSE; empty if unavailable."""
    global _MASTER_CACHE
    if _MASTER_CACHE:
        return _MASTER_CACHE
    try:
        import sqlite3

        with sqlite3.connect(f"file:{_SYMBOL_MASTER_DB}?mode=ro", uri=True) as conn:
            _MASTER_CACHE = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT symbol FROM symtoken WHERE exchange = 'NSE'"
                )
            }
    except Exception:  # noqa: BLE001 - an absent master must not abort generation
        _MASTER_CACHE = set()
    return _MASTER_CACHE


def _canonical(symbol: str, master: set[str]) -> str:
    """Repair a sanitized symbol, or raise if no repair can be confirmed.

    Fails loudly rather than emitting a guess: ``universe.json`` is a reviewed
    source of truth, and a wrong ticker that happens to resolve would mislabel
    the book rather than fail.
    """
    if symbol in _SYMBOL_OVERRIDES:
        return _SYMBOL_OVERRIDES[symbol]
    if not master or symbol in master:
        return symbol
    repaired = symbol.replace("_", "&")
    if repaired != symbol and repaired in master:
        return repaired
    raise SystemExit(
        f"screen symbol {symbol!r} is not a known NSE symbol, and no repair of "
        f"it resolves to one; fix the source CSV before regenerating"
    )


def _read_screen_symbols() -> list[str]:
    """The passing symbols, in the screen's rank order."""
    master = _nse_master()
    with SCREEN_CSV.open(newline="", encoding="utf-8") as fh:
        return [_canonical(row["Symbol"].strip(), master) for row in csv.DictReader(fh)]


def _total_r(row: dict) -> float:
    # A 0-trade symbol has a blank TotalR (the static N500 one-off has one; a
    # fresh batch report flags such symbols with no stats). It sorts last
    # rather than aborting the whole emission.
    try:
        return float(row["TotalR"])
    except (ValueError, TypeError):
        return float("-inf")


def _resolve_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map each candidate field to the actual report column that carries it.

    Raises ``ValueError`` naming the field whose column is absent, so a report
    with an unexpected schema fails loudly rather than emitting blank columns.
    """
    present = set(fieldnames)
    resolved: dict[str, str] = {}
    for field, aliases in _FIELD_SOURCE_ALIASES.items():
        match = next((alias for alias in aliases if alias in present), None)
        if match is None:
            raise ValueError(
                f"report is missing a column for {field!r}; tried {aliases} "
                f"against {sorted(present)}"
            )
        resolved[field] = match
    return resolved


def _read_candidate_rows(report_path: Path) -> list[dict]:
    """Report rows reduced to the candidate columns, sorted by TotalR desc.

    Accepts either the static N500 schema or the fresh ``batch_report.csv``
    schema (see ``_FIELD_SOURCE_ALIASES``) and always emits the canonical
    ``Symbol,Trades,TotalR,Win%,PF`` keys, so downstream readers never see the
    source's column names.
    """
    with Path(report_path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        resolved = _resolve_columns(reader.fieldnames or [])
        rows = list(reader)

    reduced = [
        {field: row[column] for field, column in resolved.items()} for row in rows
    ]
    reduced.sort(key=_total_r, reverse=True)
    return reduced


def write_universe() -> int:
    """Rewrite ``universe.json`` from the static screen; return the symbol count.

    This is the *only* place ``universe.json`` is written by the generator. The
    monthly job deliberately never calls it, so a monthly rebuild cannot undo
    whatever the weekly job promoted/dropped.
    """
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
    return len(symbols)


def write_candidates(
    report_path: Path = N500_CSV, out_path: Path = CANDIDATES_CSV
) -> int:
    """Re-emit ``candidates.csv`` from ``report_path``; return the row count.

    ``report_path`` defaults to the static ``N500_REPORT.csv`` so the standalone
    one-off reproduces the shipped pool. The monthly job passes the fresh
    ``batch_report.csv`` instead, so the rebuilt pool reflects the new backtest.
    """
    rows = _read_candidate_rows(report_path)
    with Path(out_path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CANDIDATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    n_symbols = write_universe()
    n_rows = write_candidates()

    print(f"wrote {UNIVERSE_JSON} ({n_symbols} symbols)")
    print(f"wrote {CANDIDATES_CSV} ({n_rows} candidates)")


if __name__ == "__main__":
    main()
