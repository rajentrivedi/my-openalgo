#!/usr/bin/env python3
"""OpenAlgo Python-strategy launcher for the intraday breakout bot.

Upload **this file** at ``http://127.0.0.1:5000/python`` (exchange **NSE**).
OpenAlgo's Python-strategy host runs it as its own subprocess, injecting
``OPENALGO_API_KEY`` and ``OPENALGO_HOST`` from your logged-in account -- no key
is ever pasted into source. The launcher adds the bot package to ``sys.path``
and hands control to :func:`bot.main`, which:

* refuses to run (exit 1) unless the account reports ``analyze`` (Analyzer /
  paper) mode -- so keep OpenAlgo in **Analyzer mode** for the whole session;
* places **no order** while ``OPENALGO_DRY_RUN`` is unset or ``!= "0"`` (the
  safe default the host runs with); and
* self-squares-off every position at 15:15 IST and exits 0.

Keep the bot package in place on this machine; the launcher imports it by path.
Override the location with ``INTRADAY_BREAKOUT_BOT_DIR`` for a relocated or
containerised install. Do not upload only this launcher to a different host
without the package.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

# 1. Locate the bot package (co-located with this file by default).
BOT_DIR = Path(
    os.environ.get("INTRADAY_BREAKOUT_BOT_DIR", str(Path(__file__).resolve().parent))
).resolve()
if not (BOT_DIR / "bot.py").is_file():
    raise RuntimeError(
        f"intraday_breakout_bot package not found at {BOT_DIR}; set "
        "INTRADAY_BREAKOUT_BOT_DIR to the installed package directory."
    )

# 2. The host injects OPENALGO_HOST; the bot's config reads OPENALGO_API_HOST.
#    Bridge the two so a non-default host propagates. Both default to
#    http://127.0.0.1:5000, so the out-of-the-box case needs no change.
_host = os.environ.get("OPENALGO_HOST")
if _host and not os.environ.get("OPENALGO_API_HOST"):
    os.environ["OPENALGO_API_HOST"] = _host

# 3. Log to stdout; the host redirects it to the per-strategy log file.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("intraday_breakout_launcher")


def _on_sigterm(signum, _frame):  # pragma: no cover - signal path
    """Exit cleanly on the host's stop signal.

    The host stops a strategy by sending SIGTERM to the process group (at the
    scheduled stop time or on a manual Stop). Raising SystemExit lets ``finally``
    blocks run and the log flush before the process ends.

    NOTE: a mid-session SIGTERM does NOT square off open positions -- harmless
    under the dry-run default (no orders exist), but add in-loop signal handling
    to ``bot.main`` before ever setting ``OPENALGO_DRY_RUN=0``. Because the bot
    self-squares-off at 15:15 and exits 0, a scheduled stop at/after 15:16
    normally just reaps an already-finished process.
    """
    log.info("received signal %s; shutting down launcher", signum)
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _on_sigterm)

# 4. Make the bot package importable, then run one session.
sys.path.insert(0, str(BOT_DIR))

if __name__ == "__main__":
    log.info(
        "starting intraday breakout bot from %s (host=%s, dry_run=%s)",
        BOT_DIR,
        os.environ.get("OPENALGO_API_HOST", "http://127.0.0.1:5000"),
        os.environ.get("OPENALGO_DRY_RUN", "1 (default: paper, no orders)"),
    )
    import bot  # imported here so sys.path/env are prepared first

    raise SystemExit(bot.main())
