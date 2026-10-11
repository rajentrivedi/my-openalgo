"""Make the package directory importable so tests can `import config`.

When running `python -m pytest test/test_config.py` from inside
strategies/intraday_breakout_bot/, the package directory itself is what we want
on sys.path (not the repo root), so that top-level modules like `config` import
cleanly.
"""

import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

# A throwaway placeholder so that plain `import config` works in tests that do
# not care about the key. Never a real credential. Tests that need the key
# absent use monkeypatch.delenv.
os.environ.setdefault("OPENALGO_API_KEY", "test-placeholder-key")
