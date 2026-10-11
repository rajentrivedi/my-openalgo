"""Monthly universe rebuild job for the intraday breakout bot.

Run once a month, out of session. It re-runs the full 494-symbol batch
backtest in the sibling ``intraday-strategies/`` repo (resumable, so a retry
skips symbols already scored) and then rebuilds the shipped
``data/candidates.csv`` ranked pool that the weekly job promotes from.

``run`` returns the batch backtest's exit code -- the one subprocess whose
success gates everything downstream. Several things are deliberate:

* **The batch output is redirected into this package's ``data/`` dir.**
  ``run_batch_backtest.py`` defaults ``--out-dir`` to its own parent, i.e.
  *inside* ``intraday-strategies/``. ``backtest_source.py``'s contract is that
  this bot never writes into that repo, so ``run`` passes an explicit
  ``--out-dir`` under ``config.DATA_DIR`` instead. The batch writes its headline
  one-row-per-symbol report to ``<out-dir>/batch_report.csv`` (confirmed in
  ``run_batch_backtest.main``), so that is the fresh file the rebuild reads.
* **The candidates rebuild reads the FRESH report and runs in-process.** The
  rebuild imports ``data/_gen_universe.py`` and calls its ``write_candidates``
  with ``BATCH_REPORT`` -- the file the batch just wrote -- so the full
  backtest is actually used, not discarded in favour of the static
  ``N500_REPORT.csv``. Running it in-process (rather than shelling out to the
  generator's ``main``) is what keeps the rebuild from ever rewriting
  ``universe.json``: ``write_candidates`` touches only ``candidates.csv``, so
  promotion/drop stays the weekly job's exclusive responsibility.
* **No broker or network I/O happens here, so there is nothing for ``DRY_RUN``
  to gate.** The sibling modules honour ``DRY_RUN`` only to suppress broker
  *writes*; this job runs a local, CPU-bound backtest and writes only local
  files, so it is paper-safe by construction and runs identically either way.

The external path is resolved the same way ``backtest_source.py`` does -- from
this file, not the CWD -- with an env override so a non-standard checkout can
point at its own copy.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
from pathlib import Path

import config

logger = logging.getLogger(__name__)

#: Package directory: strategies/intraday_breakout_bot/ inside the OpenAlgo repo.
PACKAGE_DIR = Path(__file__).resolve().parent

#: Environment override for the batch-backtest directory, mirroring
#: ``backtest_source.ENV_PATH``'s role for the backtest module.
ENV_BATCH_DIR = "INTRADAY_BATCH_DIR"

#: Default batch-backtest directory. The repo is <root>/openalgo, so three
#: ``.parent`` hops from this package reach <root>, whose sibling
#: ``intraday-strategies/`` holds the harness -- the exact resolution
#: ``backtest_source.DEFAULT_PATH`` uses.
_DEFAULT_BATCH_DIR = (
    PACKAGE_DIR.parent.parent.parent
    / "intraday-strategies"
    / "historify_export_20261008_184221"
)

#: The in-package generator that re-emits ``candidates.csv`` from a batch
#: report. Imported (not shelled out to) so the rebuild can pass the fresh
#: report path and so it cannot invoke the generator's universe-writing path.
GEN_CANDIDATES = PACKAGE_DIR / "data" / "_gen_universe.py"

#: Batch artefacts land here, inside the bot package, never in
#: ``intraday-strategies/``.
OUT_DIR = config.DATA_DIR / "batch_out"

#: The shipped ranked pool the weekly job promotes from. The rebuild overwrites
#: exactly this file and nothing else. Same path as ``universe.CANDIDATES_PATH``
#: and the generator's ``CANDIDATES_CSV``.
CANDIDATES_CSV = config.DATA_DIR / "candidates.csv"


def _batch_dir() -> Path:
    """Absolute path of the batch-backtest directory, env override first."""
    override = os.getenv(ENV_BATCH_DIR)
    path = Path(override).expanduser() if override else _DEFAULT_BATCH_DIR
    return path.resolve()


def _batch_report() -> Path:
    """The fresh headline report the batch writes into the forced out-dir.

    ``run_batch_backtest.main`` writes ``<out-dir>/batch_report.csv``; read
    ``OUT_DIR`` through the module global so a test can redirect the out-dir.
    """
    return OUT_DIR / "batch_report.csv"


def _load_generator():
    """Import ``data/_gen_universe.py`` by path.

    The generator has no import-time side effects, so importing it is safe and
    lets the rebuild call its candidate-emission logic directly -- reusing
    Task 8's code rather than duplicating it, and without the generator's
    ``main`` (which also rewrites ``universe.json``) ever running.
    """
    spec = importlib.util.spec_from_file_location(
        "intraday_breakout_gen_universe", GEN_CANDIDATES
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load candidate generator from {GEN_CANDIDATES}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rebuild_candidates() -> int:
    """Re-emit ``data/candidates.csv`` from the FRESH batch report.

    Returns 0 on success, non-zero otherwise. Logged but not fatal to ``run``'s
    return value, which is the batch backtest's code: a candidates rebuild that
    fails leaves the previous pool in place rather than corrupting it. Only
    ``candidates.csv`` is written -- ``universe.json`` is never touched here.
    """
    report = _batch_report()
    if not report.exists():
        logger.warning(
            "fresh batch report not found at %s; keeping the previous "
            "candidates.csv",
            report,
        )
        return 1

    logger.info("rebuilding candidates.csv from fresh batch report %s", report)
    try:
        gen = _load_generator()
        rows = gen.write_candidates(report_path=report, out_path=CANDIDATES_CSV)
    except Exception as exc:  # noqa: BLE001 - a rebuild failure must not corrupt
        logger.warning(
            "candidates rebuild failed (%s); keeping the previous candidates.csv",
            exc,
        )
        return 1
    logger.info("rebuilt %s (%d candidates) from %s", CANDIDATES_CSV, rows, report)
    return 0


def run() -> int:
    """Re-run the 494-symbol batch backtest (resumable), then rebuild candidates.

    Returns the batch backtest's exit code. Only on a clean batch run (exit 0)
    is ``candidates.csv`` rebuilt, so a failed or partial backtest can never
    overwrite the shipped pool with worse data. The rebuild reads the fresh
    ``batch_report.csv`` the batch just wrote and never rewrites
    ``universe.json``.
    """
    batch_dir = _batch_dir()
    script = batch_dir / "run_batch_backtest.py"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(script), "--resume", "--out-dir", str(OUT_DIR)]
    logger.info("monthly universe rebuild: %s (cwd=%s)", " ".join(cmd), batch_dir)
    completed = subprocess.run(cmd, cwd=str(batch_dir), check=False)

    if completed.returncode != 0:
        logger.warning(
            "batch backtest exited %s; skipping candidates rebuild",
            completed.returncode,
        )
        return completed.returncode

    _rebuild_candidates()
    return completed.returncode


__all__ = ["CANDIDATES_CSV", "GEN_CANDIDATES", "OUT_DIR", "run"]
