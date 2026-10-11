"""Monthly universe rebuild job for the intraday breakout bot.

Run once a month, out of session. It re-runs the full 494-symbol batch
backtest in the sibling ``intraday-strategies/`` repo (resumable, so a retry
skips symbols already scored) and then rebuilds the shipped
``data/candidates.csv`` ranked pool that the weekly job promotes from.

``run`` returns the batch backtest's exit code -- the one subprocess whose
success gates everything downstream. Two things are deliberate:

* **The batch output is redirected into this package's ``data/`` dir.**
  ``run_batch_backtest.py`` defaults ``--out-dir`` to its own parent, i.e.
  *inside* ``intraday-strategies/``. ``backtest_source.py``'s contract is that
  this bot never writes into that repo, so ``run`` passes an explicit
  ``--out-dir`` under ``config.DATA_DIR`` instead.
* **No broker or network I/O happens here, so there is nothing for ``DRY_RUN``
  to gate.** The sibling modules honour ``DRY_RUN`` only to suppress broker
  *writes*; this job runs a local, CPU-bound backtest and writes only local
  files, so it is paper-safe by construction and runs identically either way.

The external path is resolved the same way ``backtest_source.py`` does -- from
this file, not the CWD -- with an env override so a non-standard checkout can
point at its own copy.
"""

from __future__ import annotations

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

#: The in-package generator that re-emits ``candidates.csv`` from the ranked
#: pool. Invoked as a subprocess so its import-time work never touches this
#: process.
GEN_CANDIDATES = PACKAGE_DIR / "data" / "_gen_universe.py"

#: Batch artefacts land here, inside the bot package, never in
#: ``intraday-strategies/``.
OUT_DIR = config.DATA_DIR / "batch_out"


def _batch_dir() -> Path:
    """Absolute path of the batch-backtest directory, env override first."""
    override = os.getenv(ENV_BATCH_DIR)
    path = Path(override).expanduser() if override else _DEFAULT_BATCH_DIR
    return path.resolve()


def _rebuild_candidates() -> int:
    """Re-emit ``data/candidates.csv`` from the refreshed ranked pool.

    Returns the generator's exit code. Logged but not fatal to ``run``'s
    return value, which is the batch backtest's code: a candidates rebuild
    that fails leaves the previous pool in place rather than corrupting it.
    """
    cmd = [sys.executable, str(GEN_CANDIDATES)]
    logger.info("rebuilding candidates.csv: %s", " ".join(cmd))
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        logger.warning(
            "candidates rebuild exited %s; keeping the previous candidates.csv",
            completed.returncode,
        )
    return completed.returncode


def run() -> int:
    """Re-run the 494-symbol batch backtest (resumable), then rebuild candidates.

    Returns the batch backtest's exit code. Only on a clean batch run (exit 0)
    is ``candidates.csv`` rebuilt, so a failed or partial backtest can never
    overwrite the shipped pool with worse data.
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


__all__ = ["GEN_CANDIDATES", "OUT_DIR", "run"]
