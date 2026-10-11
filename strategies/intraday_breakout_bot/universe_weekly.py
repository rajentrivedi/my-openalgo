"""Weekly universe refresh job for the intraday breakout bot.

Run once a week after the market close. It re-screens the current universe
against the latest backtest metrics, drops symbols that no longer clear
``universe.THRESHOLDS``, promotes the highest-ranked passing candidates to
refill the book up to ``target_size``, writes the new ``universe.json`` and a
dated ``changes_YYYY-MM-DD.csv`` audit trail, and sends the operator a single
WhatsApp summary.

Three things here are load-bearing:

* **``CANDIDATES`` is a module-level global, read at call time.** The weekly
  test monkeypatches ``universe_weekly.CANDIDATES`` to a ``tmp_path`` CSV, so
  ``run`` must read it through the module global rather than a captured
  default. ``universe``'s own ``CANDIDATES_PATH`` is a *different* global that
  the test does not patch; promoting from it would read the shipped file
  instead of the test's. This module therefore owns its own promotion-pool
  reader and never routes the pool through ``universe._candidate_metrics``.
* **The promotion pool is read tolerant of both column namings.** The shipped
  ``data/candidates.csv`` (Task 8) carries the N500 column names
  ``Symbol/Trades/TotalR/Win%/PF``; the weekly test writes the lowercase
  row-keys ``symbol/trades/total_r/win_rate_pct/pf`` directly. ``_read_pool``
  normalises either into the lowercase keys that ``universe.passes`` consumes,
  so the same code path serves production and the test.
* **The WhatsApp send is gated on ``config.DRY_RUN``.** ``notify.send`` does
  not no-op under ``DRY_RUN`` or a placeholder key -- it would attempt a
  (loopback) HTTP call. The weekly test sets up no real credentials and does
  not stub ``notify``, so the send is suppressed whenever ``DRY_RUN`` is on
  (its default). With ``OPENALGO_DRY_RUN=0`` the summary goes out for real,
  mirroring how ``executor`` only writes to the broker once dry-run is off.
"""

from __future__ import annotations

import csv
import datetime
import logging
from pathlib import Path

import config
import notify
import universe

logger = logging.getLogger(__name__)

#: Module-level and deliberately rebindable: the weekly test monkeypatches this
#: to a ``tmp_path`` CSV. Read through the module global in ``run`` so the patch
#: takes effect; never captured into a default argument. Defaults to the shipped
#: ranked pool, the same file ``universe.CANDIDATES_PATH`` points at.
CANDIDATES = config.DATA_DIR / "candidates.csv"

#: Maps every candidate-file column this job understands to the lowercase
#: metrics-row key ``universe.passes`` reads. Both the shipped N500 column
#: names and the already-lowercase row keys are accepted, so one reader serves
#: the production file and the test's hand-written CSV.
_COLUMN_ALIASES = {
    "symbol": "symbol",
    "Symbol": "symbol",
    "trades": "trades",
    "Trades": "trades",
    "total_r": "total_r",
    "TotalR": "total_r",
    "win_rate_pct": "win_rate_pct",
    "Win%": "win_rate_pct",
    "pf": "pf",
    "PF": "pf",
}

#: Coercion for each numeric metric key. ``trades`` goes through ``float``
#: first so a value written as ``40.0`` still lands as an ``int``.
_METRIC_COERCE = {
    "trades": lambda v: int(float(v)),
    "total_r": float,
    "win_rate_pct": float,
    "pf": float,
}


def _read_pool(path: Path | str) -> list[tuple[str, dict]]:
    """Return ``(symbol, metrics)`` pairs from the candidate file, in file order.

    File order *is* promotion order: ``candidates.csv`` is emitted sorted by
    ``TotalR`` descending, so the first passing, not-already-present symbol is
    the highest-ranked one to promote. Fails closed on a missing file (raising
    ``RuntimeError``, consistent with ``universe.load``) rather than yielding an
    empty pool that would silently drop every symbol. A blank metric cell is
    left out of the row, so the key defaults to a value that fails its gate in
    ``universe.passes`` -- the symbol stays in the pool but can never be
    promoted on incomplete data.
    """
    path = Path(path)
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"candidates file not found at {path}; cannot refresh the universe"
        ) from exc

    pool: list[tuple[str, dict]] = []
    for row in rows:
        symbol: str | None = None
        metrics: dict = {}
        for column, raw in row.items():
            key = _COLUMN_ALIASES.get(column)
            if key is None:
                continue
            if key == "symbol":
                symbol = (raw or "").strip() or None
                continue
            if raw is None or str(raw).strip() == "":
                continue
            try:
                metrics[key] = _METRIC_COERCE[key](raw)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"candidates file {path} is malformed near symbol "
                    f"{symbol!r}: {column}={raw!r} ({exc})"
                ) from exc
        if symbol:
            pool.append((symbol, metrics))
    return pool


def _changes_path() -> Path:
    """Path of today's ``changes_YYYY-MM-DD.csv``, in the universe's directory.

    Derived from ``universe.UNIVERSE_PATH``'s parent (``data/`` in production,
    the ``tmp_path`` the test patches it to otherwise). Deriving it keeps the
    weekly test hermetic -- the audit file lands beside the temp universe, never
    in the shipped ``data/`` dir -- while still writing to ``data/`` for real.
    """
    directory = Path(universe.UNIVERSE_PATH).parent
    directory.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    return directory / f"changes_{today}.csv"


def _write_changes(kept: list[str], dropped: list[str], promoted: list[str]) -> Path:
    """Write the dated audit trail: one ``action,symbol`` row per change."""
    path = _changes_path()
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["action", "symbol"])
        for symbol in promoted:
            writer.writerow(["promoted", symbol])
        for symbol in dropped:
            writer.writerow(["dropped", symbol])
        for symbol in kept:
            writer.writerow(["kept", symbol])
    return path


def _summary(kept: list[str], dropped: list[str], promoted: list[str]) -> str:
    """Build the single-message WhatsApp summary of the week's changes."""
    lines = [
        "Intraday breakout bot -- weekly universe update",
        f"kept: {len(kept)}  dropped: {len(dropped)}  promoted: {len(promoted)}",
    ]
    if promoted:
        lines.append("Promoted: " + ", ".join(promoted))
    if dropped:
        lines.append("Dropped: " + ", ".join(dropped))
    return "\n".join(lines)


def _send_summary(kept: list[str], dropped: list[str], promoted: list[str]) -> bool:
    """Send the summary, unless dry-run suppresses it. Never raises.

    ``config.DRY_RUN`` is read through the module at call time so a test (or an
    operator) can flip it. When it is on, the summary is logged instead of sent,
    so no network call is made -- which is what keeps the weekly test, running
    with the default dry-run and placeholder credentials, from touching the
    WhatsApp bridge. A real failure degrades to ``False`` in a log line, exactly
    like ``notify.send`` and ``executor._notify``.
    """
    message = _summary(kept, dropped, promoted)
    if config.DRY_RUN:
        logger.info(
            "DRY_RUN weekly summary (not sent): %s", message.replace("\n", " | ")
        )
        return False
    try:
        return notify.send(message)
    except Exception as exc:  # noqa: BLE001 - notification is courtesy, never fatal
        logger.warning("weekly summary notification failed: %s", exc)
        return False


def run(target_size: int = 64, *, persist: bool = True) -> dict:
    """Re-screen the universe, refill to ``target_size``, persist and notify.

    Returns ``{"kept": [...], "dropped": [...], "promoted": [...]}``.

    * ``kept`` -- current symbols that still clear ``universe.THRESHOLDS`` on
      their latest backtest metrics.
    * ``dropped`` -- current symbols that no longer pass (or have no candidate
      row at all). Keep/drop uses backtest metrics via ``universe.passes``, not
      live P&L: the gate is "does this still look like a validated edge".
    * ``promoted`` -- the highest-ranked passing candidates not already present,
      added until the book reaches ``target_size``.

    The new universe is written as ``promoted`` first, then ``kept``, so the
    freshest edges head the list. Also writes ``changes_YYYY-MM-DD.csv`` and
    sends (or, under dry-run, logs) a one-line WhatsApp summary.

    ``persist`` is the operator preview seam: with ``persist=False`` (the CLI's
    ``--dry-run``) the keep/drop/promote decision is computed and returned but
    **nothing is written** -- no ``universe.json``, no ``changes_*.csv``, no
    notification -- so an operator can see what the refresh *would* do before
    letting it mutate the live book. The scheduled service always runs with the
    default ``persist=True``.
    """
    current = universe.load()
    pool = _read_pool(CANDIDATES)
    metrics_by_symbol = dict(pool)

    kept: list[str] = []
    dropped: list[str] = []
    for symbol in current:
        if universe.passes(metrics_by_symbol.get(symbol, {})):
            kept.append(symbol)
        else:
            dropped.append(symbol)

    promoted: list[str] = []
    present = set(current)
    for symbol, metrics in pool:
        if len(kept) + len(promoted) >= target_size:
            break
        if symbol in present or symbol in promoted:
            continue
        if universe.passes(metrics):
            promoted.append(symbol)

    new_universe = [*promoted, *kept]
    today = datetime.date.today().isoformat()
    if persist:
        universe.save(
            new_universe,
            note=f"weekly refresh {today}: +{len(promoted)} / -{len(dropped)}",
        )
        _write_changes(kept, dropped, promoted)
        _send_summary(kept, dropped, promoted)
    else:
        logger.info(
            "dry-run weekly refresh: would keep %d, drop %d, promote %d; "
            "nothing written",
            len(kept),
            len(dropped),
            len(promoted),
        )

    return {"kept": kept, "dropped": dropped, "promoted": promoted}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the weekly refresh. Returns a process exit code.

    This is what the ``intraday-breakout-universe.service`` unit runs
    (``python3 -m universe_weekly``) and what an operator runs by hand to
    preview a refresh (``python3 -m universe_weekly --dry-run``). ``--dry-run``
    maps to ``run(persist=False)``: it prints the kept/dropped/promoted summary
    and writes nothing.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="universe_weekly",
        description="Weekly universe re-screen for the intraday breakout bot.",
    )
    parser.add_argument(
        "--target-size",
        type=int,
        default=64,
        help="target universe size after the refresh (default: 64).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and print the changes without writing universe.json / "
        "changes_*.csv or sending a notification.",
    )
    args = parser.parse_args(argv)

    result = run(target_size=args.target_size, persist=not args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}kept: {len(result['kept'])}  "
        f"dropped: {len(result['dropped'])}  "
        f"promoted: {len(result['promoted'])}"
    )
    if result["promoted"]:
        print("promoted: " + ", ".join(result["promoted"]))
    if result["dropped"]:
        print("dropped: " + ", ".join(result["dropped"]))
    return 0


__all__ = ["CANDIDATES", "main", "run"]


if __name__ == "__main__":  # pragma: no cover - process entry point
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
