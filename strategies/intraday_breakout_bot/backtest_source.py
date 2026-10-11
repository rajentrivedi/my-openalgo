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
  is registered under a synthetic name (`_MODULE_NAME`) rather than
  "__main__".
* The resolved path is checked for existence *before* importing, so a missing
  file fails with a clear `FileNotFoundError` naming the path instead of an
  opaque `FileNotFoundError` from the import machinery.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # keeps pandas out of the loader's import-time dependencies
    import pandas as pd

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


@contextlib.contextmanager
def _pin_sdk_openalgo() -> Iterator[None]:
    """Make `import openalgo` resolve to the installed SDK, not the server repo.

    The backtest module executes `from openalgo import ta` at import time. That
    import is the loader's dependency, so the loader owns making it resolve to
    the SDK.

    This is a context manager, not a plain function, because the SDK has to stay
    pinned for the whole duration of `exec_module` -- not just while the
    import is forced -- and because both `sys.path` and `sys.modules` are
    process-global state that must be handed back exactly as found on every
    path, including a failing one. Anything the caller does inside the `with`
    body is therefore covered by the restore.

    Restoration is exact:

    * `sys.path` entries are re-inserted at their original indices, not
      appended, so import priority is unchanged for the rest of the process.
    * `sys.modules["openalgo"]` is put back to the object that was there before
      (a plain `setdefault` cannot do this: the SDK import has already claimed
      the key by the time we restore). If nothing was there before, the key is
      removed again rather than left pointing at the SDK.

    This keeps the server repo's own tests, which rely on the repo root being
    importable as `openalgo`, unaffected.
    """
    existing = sys.modules.get("openalgo")
    if existing is not None and hasattr(existing, "ta"):
        yield
        return

    if not any(_is_server_repo_entry(e) for e in sys.path):
        yield
        return

    # (index, entry) pairs for the shadowing entries, so each can be put back
    # where it came from rather than appended to the tail of sys.path.
    positions: list[tuple[int, str]] = []
    previous_openalgo = None
    try:
        # Both mutations live inside the try so that the restore in `finally`
        # covers every path out of this block, including anything a future
        # edit inserts between here and the yield.
        positions = [
            (i, e) for i, e in enumerate(sys.path) if _is_server_repo_entry(e)
        ]
        for _, entry in positions:
            sys.path.remove(entry)
        previous_openalgo = sys.modules.pop("openalgo", None)
        import openalgo  # noqa: F401

        if not hasattr(openalgo, "ta"):
            raise ImportError(
                "The installed `openalgo` package does not expose `ta`, which "
                "the backtest module imports. The OpenAlgo SDK is required to "
                "run the strategy."
            )
        yield
    finally:
        # The body may itself have touched sys.path, so drop any shadowing
        # entry still present before reinserting; removing one that is already
        # gone would raise and mask the real outcome.
        for _, entry in positions:
            while entry in sys.path:
                sys.path.remove(entry)
        # Re-insert in ascending original-index order, which reproduces the
        # exact original list because each insertion shifts only later indices.
        for index, entry in sorted(positions):
            sys.path.insert(index, entry)
        # Explicit assignment, not setdefault: `import openalgo` above has
        # already installed the SDK under this key, so setdefault would silently
        # discard the value we are trying to put back.
        if previous_openalgo is None:
            sys.modules.pop("openalgo", None)
        else:
            sys.modules["openalgo"] = previous_openalgo


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
    # Register before exec_module, exactly as the import machinery does, so
    # backtest code that resolves `cls.__module__` or imports itself by name
    # during its own execution finds the (partially initialised) module.
    sys.modules[_MODULE_NAME] = module
    try:
        with _pin_sdk_openalgo():
            spec.loader.exec_module(module)
    except BaseException:
        # Do not leave a half-initialised module behind under a name other code
        # could import.
        sys.modules.pop(_MODULE_NAME, None)
        raise

    _CACHE = module
    _CACHE_PATH = path
    return module


def args() -> argparse.Namespace:
    """The validated backtest parameters (the strategy's contract)."""
    return load().make_args()


def plan_for_day(
    df: pd.DataFrame,
    day: datetime.date,
    plan_args: argparse.Namespace,
) -> tuple[str, dict, pd.Timestamp] | None:
    """Delegate to the backtest's `plan_for_day`.

    The third parameter is named `plan_args` rather than `args` so it cannot
    shadow the module-level `args()` function within this body, which would
    otherwise make `args` here mean two different things.
    """
    return load().plan_for_day(df, day, plan_args)


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


# `IST` is supplied by the module-level `__getattr__` above, so static checkers
# cannot see it defined here; `from backtest_source import *` still resolves it.
__all__ = [  # noqa: F822
    "IST",
    "args",
    "load",
    "plan_for_day",
]
