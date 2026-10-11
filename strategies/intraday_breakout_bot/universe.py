"""Universe management for the intraday breakout bot.

The tradable universe is the set of symbols that cleared the validated
backtest screen (``THRESHOLDS``). It ships as ``data/universe.json`` and is the
single source of truth for which symbols the planner may act on.

Three things here are load-bearing:

* **``UNIVERSE_PATH`` is read as a module-level global at call time.** Tests
  monkeypatch ``universe.UNIVERSE_PATH`` to a ``tmp_path``; capturing it into a
  default argument would leave those tests reading the real shipped file. This
  mirrors ``journal.JOURNAL_DIR``.
* **``load`` fails closed.** A missing file, malformed JSON, or a ``symbols``
  value that is not a list of strings raises ``RuntimeError`` -- it never
  returns a partial or empty list. A universe silently reduced to a subset
  would let the bot trade a different book than the one that was validated,
  which is worse than refusing to start.
* **The N500 report's column names are mapped once, here.** ``score`` and
  ``refresh`` consume the shipped ``data/candidates.csv`` (the re-emitted N500
  report) and translate its ``Trades/TotalR/Win%/PF`` columns into the
  lowercase row keys ``passes`` expects (``trades/total_r/win_rate_pct/pf``).
  Task 10 depends on this mapping, so it lives in exactly one place.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
from pathlib import Path

import config
import journal

#: Module-level and deliberately rebindable: tests monkeypatch this to a
#: ``tmp_path``. Read at call time via the global, never captured into a
#: default argument. Mirrors ``journal.JOURNAL_DIR``.
UNIVERSE_PATH = config.UNIVERSE_PATH

#: The ranked candidate pool, shipped alongside the universe. This is the N500
#: report re-emitted by ``data/_gen_universe.py`` with the candidate columns,
#: sorted by TotalR descending. ``score``/``refresh`` read it rather than the
#: external backtest repo so the package is self-contained at runtime.
CANDIDATES_PATH = config.DATA_DIR / "candidates.csv"

#: The exact screen that produced ``universe.json``. These four gates are the
#: contract: a row must clear every one of them (strict ``>=``) to belong in
#: the universe. Any change here is a change to what the bot is allowed to
#: trade, so it is a deliberate, reviewed edit -- not a tunable.
THRESHOLDS = {
    "min_trades": 30,
    "min_total_r": 8.0,
    "min_win_rate": 50.0,
    "min_pf": 1.5,
}

#: Maps a ``candidates.csv`` column to the lowercase metrics-row key, with the
#: coercion each needs. Defined once so ``_candidate_metrics`` and any future
#: reader cannot disagree about the mapping.
_COLUMN_MAP = {
    "Trades": ("trades", lambda v: int(float(v))),
    "TotalR": ("total_r", float),
    "Win%": ("win_rate_pct", float),
    "PF": ("pf", float),
}


def load() -> list[str]:
    """Return the shipped universe symbols, or raise ``RuntimeError``.

    Fails closed on every abnormal input -- missing file, invalid JSON, or a
    ``symbols`` field that is not a list of strings. It never returns a partial
    list: a half-read universe would silently narrow the book the bot trades.
    """
    path = Path(UNIVERSE_PATH)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"universe file not found at {path}; refusing to start with no "
            "universe"
        ) from exc
    except OSError as exc:  # pragma: no cover - defensive on unreadable file
        raise RuntimeError(f"universe file {path} could not be read: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"universe file {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"universe file {path} is malformed: expected a JSON object, got "
            f"{type(data).__name__}"
        )
    symbols = data.get("symbols")
    if not isinstance(symbols, list):
        raise RuntimeError(
            f"universe file {path} is malformed: 'symbols' must be a list, got "
            f"{type(symbols).__name__}"
        )
    if not all(isinstance(s, str) for s in symbols):
        raise RuntimeError(
            f"universe file {path} is malformed: 'symbols' must be a list of "
            "strings"
        )
    return symbols


def save(symbols: list[str], note: str = "") -> None:
    """Write ``symbols`` to ``UNIVERSE_PATH`` as the shipped JSON shape.

    ``note`` has a default so both ``save(syms)`` and ``save(syms, "msg")``
    work. The write is atomic (temp file + ``os.replace``) so an interrupt
    cannot leave a truncated ``universe.json`` that ``load`` would later reject
    and refuse to start on.
    """
    path = Path(UNIVERSE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.date.today().isoformat(),
        "note": note,
        "symbols": list(symbols),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def passes(row: dict) -> bool:
    """True iff a metrics ``row`` clears all four ``THRESHOLDS`` (strict ``>=``).

    Reads the lowercase keys ``trades``, ``total_r``, ``win_rate_pct`` and
    ``pf``. A missing key defaults to a value that fails its gate, so an
    incomplete row is rejected rather than raising -- fail-closed, consistent
    with ``load``.
    """
    return (
        row.get("trades", 0) >= THRESHOLDS["min_trades"]
        and row.get("total_r", 0.0) >= THRESHOLDS["min_total_r"]
        and row.get("win_rate_pct", 0.0) >= THRESHOLDS["min_win_rate"]
        and row.get("pf", 0.0) >= THRESHOLDS["min_pf"]
    )


def _candidate_metrics() -> dict[str, dict]:
    """``{symbol: {trades, total_r, win_rate_pct, pf}}`` from candidates.csv.

    This is the one place the N500 column names are translated to the lowercase
    row keys ``passes`` expects. Fails closed: a missing or malformed candidate
    file raises ``RuntimeError`` rather than yielding an empty pool that would
    silently drop every symbol in ``refresh``.
    """
    path = Path(CANDIDATES_PATH)
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"candidates file not found at {path}; cannot score the universe"
        ) from exc

    out: dict[str, dict] = {}
    for row in rows:
        metrics: dict = {}
        for column, (key, coerce) in _COLUMN_MAP.items():
            try:
                cell = row[column]
            except KeyError as exc:
                raise RuntimeError(
                    f"candidates file {path} is missing column {exc}"
                ) from exc
            if cell is None or cell.strip() == "":
                # A blank metric (e.g. the N500 0-trade row) is left out, so
                # the key defaults to a value that fails its gate in `passes`
                # -- the symbol stays in the pool but can never be promoted.
                continue
            try:
                metrics[key] = coerce(cell)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"candidates file {path} is malformed near symbol "
                    f"{row.get('Symbol')!r}: {column}={cell!r} ({exc})"
                ) from exc
        out[row["Symbol"]] = metrics
    return out


def _ranked_candidates() -> list[tuple[str, dict]]:
    """Candidate ``(symbol, metrics)`` pairs in TotalR-descending order.

    ``candidates.csv`` is already sorted by ``TotalR`` descending (that is how
    ``_gen_universe`` emits it), so file order is the promotion order.
    """
    path = Path(CANDIDATES_PATH)
    metrics = _candidate_metrics()
    with path.open(newline="", encoding="utf-8") as fh:
        order = [row["Symbol"] for row in csv.DictReader(fh)]
    return [(sym, metrics[sym]) for sym in order]


def _live_pnl(frame, symbol: str) -> dict:
    """Live realised P&L for ``symbol`` from the decision journal.

    Sums the journal's ``pnl`` and ``r_multiple`` over the symbol's rows and
    counts rows that carry a realised ``pnl`` as live trades. An empty journal
    yields zeros, not a missing key, so ``score`` always returns a uniform
    shape.
    """
    zero = {"live_pnl": 0.0, "live_r": 0.0, "live_trades": 0}
    if frame is None or frame.empty or "symbol" not in frame:
        return zero
    sub = frame[frame["symbol"] == symbol]
    if sub.empty:
        return zero
    pnl = float(sub["pnl"].sum(skipna=True)) if "pnl" in sub else 0.0
    r = float(sub["r_multiple"].sum(skipna=True)) if "r_multiple" in sub else 0.0
    trades = int(sub["pnl"].notna().sum()) if "pnl" in sub else 0
    return {"live_pnl": pnl, "live_r": r, "live_trades": trades}


def score(symbols: list[str]) -> dict[str, dict]:
    """Merge backtest metrics with live journal P&L, per symbol.

    For each symbol: the backtest metrics from ``candidates.csv`` (lowercase
    keys, via ``_candidate_metrics``) merged with the live realised figures
    from the journal (``live_pnl``, ``live_r``, ``live_trades``). A symbol with
    no candidate row still appears, carrying only its live figures, so callers
    see every symbol they asked about.
    """
    backtest = _candidate_metrics()
    frame = journal.read_all()
    scored: dict[str, dict] = {}
    for symbol in symbols:
        metrics = dict(backtest.get(symbol, {}))
        metrics.update(_live_pnl(frame, symbol))
        scored[symbol] = metrics
    return scored


def refresh(
    symbols: list[str], target_size: int = 64
) -> tuple[list[str], list[str], list[str]]:
    """Re-screen ``symbols`` and top the universe back up to ``target_size``.

    Returns ``(kept, dropped, promoted)``:

    * ``kept`` -- current symbols that still clear ``THRESHOLDS`` on their
      latest backtest metrics.
    * ``dropped`` -- current symbols that no longer pass (or have no candidate
      row at all).
    * ``promoted`` -- the highest-ranked passing symbols from the candidate
      pool, not already in the universe, added until the universe reaches
      ``target_size``.

    Keep/drop uses the backtest metrics (``passes``), not live P&L: the gate is
    "does this still look like a validated edge", which is what the screen
    measured. Live figures live on ``score`` for the weekly review.
    """
    backtest = _candidate_metrics()
    kept: list[str] = []
    dropped: list[str] = []
    for symbol in symbols:
        if passes(backtest.get(symbol, {})):
            kept.append(symbol)
        else:
            dropped.append(symbol)

    promoted: list[str] = []
    current = set(symbols)
    for symbol, metrics in _ranked_candidates():
        if len(kept) + len(promoted) >= target_size:
            break
        if symbol in current or symbol in promoted:
            continue
        if passes(metrics):
            promoted.append(symbol)

    return kept, dropped, promoted


__all__ = [
    "CANDIDATES_PATH",
    "THRESHOLDS",
    "UNIVERSE_PATH",
    "load",
    "passes",
    "refresh",
    "save",
    "score",
]
