"""Intraday breakout bot configuration.

Account mode is paper trading (OpenAlgo `analyze`). This module never places an
order; `DRY_RUN` defaults to True and must be explicitly overridden with
OPENALGO_DRY_RUN=0 by an operator who understands the consequence.
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Execution safety -------------------------------------------------------

#: True unless OPENALGO_DRY_RUN=0 is explicitly set.
DRY_RUN = os.getenv("OPENALGO_DRY_RUN", "1") != "0"

# --- API connection ---------------------------------------------------------

API_HOST = os.getenv("OPENALGO_API_HOST", "http://127.0.0.1:5000")
WS_URL = os.getenv("OPENALGO_WS_URL", "ws://127.0.0.1:8765")

# --- Risk and capital -------------------------------------------------------

BUDGET = 100000.0
RISK_PCT = 1.0
MAX_CONCURRENT = 4
DAILY_LOSS_LIMIT = 3000.0

# --- Instrument / session ---------------------------------------------------

EXCHANGE = "NSE"
PRODUCT = "MIS"
PLAN_TIME = "10:10"
SQUAREOFF_TIME = "15:15"
SESSION_START = "09:15"

# --- Paths ------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_PATH = DATA_DIR / "universe.json"
JOURNAL_DIR = DATA_DIR / "journal"
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

# --- Timezone ---------------------------------------------------------------

TZ = ZoneInfo("Asia/Kolkata")

# --- API key ----------------------------------------------------------------

API_KEY = os.getenv("OPENALGO_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "OPENALGO_API_KEY is not set. Export it before importing config; "
        "never hardcode it in source."
    )


def assert_paper_only() -> None:
    """Fail loudly unless the connected account is in `analyze` (paper) mode.

    The openalgo SDK is imported lazily so that `import config` never needs the
    SDK installed and never performs network I/O.
    """
    from openalgo import api as openalgo_api

    client = openalgo_api(api_key=API_KEY, host=API_HOST)
    response = client.funds()
    mode = (response or {}).get("mode")
    if mode != "analyze":
        raise RuntimeError(
            f"Account mode is {mode!r}, expected 'analyze' (paper trading). "
            "Refusing to continue."
        )


__all__ = [
    "API_HOST",
    "API_KEY",
    "BUDGET",
    "DAILY_LOSS_LIMIT",
    "DATA_DIR",
    "DRY_RUN",
    "EXCHANGE",
    "JOURNAL_DIR",
    "MAX_CONCURRENT",
    "PACKAGE_DIR",
    "PLAN_TIME",
    "PRODUCT",
    "RISK_PCT",
    "SESSION_START",
    "SQUAREOFF_TIME",
    "TZ",
    "UNIVERSE_PATH",
    "WS_URL",
    "assert_paper_only",
]
