"""Test configuration scoped to strategies/intraday_breakout_bot/test/.

Two jobs:

1. Put the bot package directory on sys.path so tests can `import config`.
   A plain module-level `sys.path.insert(0)` would be process-global: pytest
   imports this conftest once per session, so *every* test in the repo could
   resolve a generic top-level name `config` to this module.

   Instead an autouse fixture (which pytest applies only to tests in this
   directory) makes the path live for the duration of each test here, and a
   session-finish hook removes it again. Combined with dropping the cached
   `config` module, an unrelated test doing `import config` fails loudly with
   ModuleNotFoundError instead of silently receiving the bot's config.

2. Provide a placeholder API key for the whole session so that a bare
   `import config` works in tests that do not care about the key. Tests that
   need it absent use monkeypatch.delenv, which restores the placeholder
   afterwards.

Later tasks add their own test files under this same directory and inherit
both behaviours automatically.
"""

import os
import sys

import pytest

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(TEST_DIR)


@pytest.fixture(autouse=True)
def _package_dir_on_syspath():
    """Expose the bot package dir to this directory's tests only.

    Also evicts the cached `config` module on the way out: removing the path is
    not enough, because `import config` caches the module in sys.modules for
    the rest of the session, which would let unrelated tests keep resolving the
    generic top-level name to the bot's module.
    """
    sys.path.append(PACKAGE_DIR)
    try:
        yield
    finally:
        try:
            sys.path.remove(PACKAGE_DIR)
        except ValueError:
            pass
        sys.modules.pop("config", None)


def pytest_sessionfinish(session, exitstatus):
    """Leave no trace of the temporary import path or cached module behind."""
    try:
        sys.path.remove(PACKAGE_DIR)
    except ValueError:
        pass
    sys.modules.pop("config", None)


# A throwaway placeholder so that plain `import config` works in tests that do
# not care about the key. Never a real credential.
os.environ.setdefault("OPENALGO_API_KEY", "test-placeholder-key")
