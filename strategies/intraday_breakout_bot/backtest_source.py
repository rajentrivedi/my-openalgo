"""Loader for the validated intraday breakout backtest module.

The strategy math lives in a standalone backtest script outside this repo
(`../intraday-strategies/historify_export_20261008_184221/`). This bot reuses
that exact code rather than reimplementing it, so live signals and the
validated backtest cannot drift apart.

Rules this module obeys:

* The backtest file is read-only. We import it; we never edit it and never
  write anything into `intraday-strategies/`.
* Importing the module executes its imports but not its `main()`, which is
  guarded by `if __name__ == "__main__"`. To keep that guarantee, the module
  is registered under a synthetic name ("bt") rather than "__main__".
* The resolved path is checked for existence *before* importing, so a missing
  file fails with a clear `FileNotFoundError` naming the path instead of an
  opaque `FileNotFoundError` from the import machinery.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

#: Environment variable that overrides the backtest module location.
ENV_PATH = "INTRADAY_BACKTEST_PATH"

#: Package directory: strategies/intraday_breakout_bot/ inside the OpenAlgo repo.
PACKAGE_DIR = Path(__file__).resolve().parent

#: Default backtest module, resolved from this file (not the CWD) so the bot
#: works regardless of where it is launched from.
#:
#: The repo is <root>/openalgo, so from <root>/openalgo/strategies/
#: intraday_breakout_bot three `.parent` hops reach <root>, whose sibling
#: `intraday-strategies/` holds the backtest. (The task brief's example showed
#: two hops; that resolves to <root>/openalgo/historify_export_... which does
#: not exist.)
DEFAULT_PATH = (
    PACKAGE_DIR.parent.parent.parent
    / "intraday-strategies"
    / "historify_export_20261008_184221"
    / "deepseek_python_20261009_b8341b.py"
)

# Synthetic module name. Deliberately not "__main__", which would let the
# backtest's own `if __name__ == "__main__"` guard fire and run the backtest.
_MODULE_NAME = "intraday_breaktest_module"

#: Cached backtest module, and the path it was loaded from.
#:
#: The path is cached alongside the module so that changing
#: INTRADAY_BACKTEST_PATH cannot silently hand back a module built from the old
#: file: a fresh path forces a reload.
_CACHE = None
_CACHE_PATH = None


def _resolve_path() -> Path:
    """Absolute path of the backtest module, from the env var or the default."""
    override = os.getenv(ENV_PATH)
    path = Path(override).expanduser() if override else DEFAULT_PATH
    return path.resolve()


def _is_server_repo_entry(entry: str) -> bool:
    """True if `entry` is the parent of the OpenAlgo *server* repo checkout.

    The server repo root contains an (empty) `__init__.py`, so it is itself an
    importable package whose name is `openalgo`. Any `sys.path` entry pointing
    at that repo's *parent* therefore makes `import openalgo` return the server
    application instead of the installed SDK. Fingerprinted structurally rather
    than by name so an unrelated directory cannot be mistaken for it.
    """
    try:
        root = Path(entry or ".").resolve() / "openalgo"
    except OSError:  # pragma: no cover - defensive, bad sys.path entry
        return False
    return (root / "__init__.py").is_file() and (root / "strategies").is_dir()


def _pin_sdk_openalgo() -> None:
    """Make `import openalgo` resolve to the installed SDK, not the server repo.

    The backtest module executes `from openalgo import ta` at import time. That
    import is the loader's dependency, so the loader owns making it resolve to
    the SDK. Shadowing entries are hidden only for the duration of the backtest
    import and restored immediately afterwards, so the rest of the process (and
    the server repo's own tests, which rely on the repo root being importable)
    is unaffected.
    """
    existing = sys.modules.get("openalgo")
    if existing is not None and hasattr(existing, "ta"):
        return

    shadowing = [e for e in sys.path if _is_server_repo_entry(e)]
    if not shadowing:
        return

    for entry in shadowing:
        sys.path.remove(entry)
    popped = sys.modules.pop("openalgo", None)
    try:
        import openalgo  # noqa: F401

        if not hasattr(openalgo, "ta"):
            raise ImportError(
                "The installed `openalgo` package does not expose `ta`, which "
                "the backtest module imports. The OpenAlgo SDK is required to "
                "run the strategy."
            )
    finally:
        if popped is not None:
            sys.modules.setdefault("openalgo", popped)
        sys.path.extend(shadowing)


def load() -> Any:
    """Import and return the backtest module. Cached per resolved path."""
    global _CACHE, _CACHE_PATH

    path = _resolve_path()
    if _CACHE is not None and _CACHE_PATH == path:
        return _CACHE

    if not path.is_file():
        raise FileNotFoundError(
            f"Backtest module not found at {path}. Set {ENV_PATH} to the "
            f"correct file (expected default: {DEFAULT_PATH})."
        )

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load backtest module from {path}")
    module = importlib.util.module_from_spec(spec)
    _pin_sdk_openalgo()
    spec.loader.exec_module(module)

    _CACHE = module
    _CACHE_PATH = path
    return module


def args() -> argparse.Namespace:
    """The validated backtest parameters (the strategy's contract)."""
    return load().make_args()


def plan_for_day(
    df, day: datetime.date, args: argparse.Namespace
) -> tuple[str, dict, Any] | None:
    """Delegate to the backtest's `plan_for_day`."""
    return load().plan_for_day(df, day, args)


def __getattr__(name: str) -> Any:
    """Expose the backtest's module-level constants lazily (PEP 562).

    `IST` is delegated rather than redefined so the bot and the backtest can
    never disagree about the exchange timezone. Lazy resolution keeps
    `import backtest_source` free of side effects: the backtest is not
    imported (and a bad path cannot raise) until something is actually used.
    """
    if name == "IST":
        return load().IST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["IST", "args", "load", "plan_for_day"]