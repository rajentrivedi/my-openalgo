import builtins
import importlib
import sys

import backtest_source
import pytest


def _seed_shadowing_repo_root(tmp_path):
    """Build a fake OpenAlgo server repo parent and return its path.

    `_is_server_repo_entry` fingerprints a shadowing `sys.path` entry
    structurally: it needs `<entry>/openalgo/__init__.py` to be a file and
    `<entry>/openalgo/strategies` to be a directory. This fixture creates
    exactly that shape, so the pin's shadowing logic can be exercised without
    depending on the real repository being present on the machine.
    """
    root = tmp_path / "server_repo_parent"
    (root / "openalgo" / "strategies").mkdir(parents=True)
    (root / "openalgo" / "__init__.py").write_text("")
    return root


def _sentinel_module():
    """A stand-in for the server repo's `openalgo` package: no `ta` attribute."""
    module = type(sys)("openalgo")
    return module


def _pin_with_shadowing(monkeypatch, tmp_path):
    """Put the shadowing condition in place and return (root, sentinel)."""
    root = _seed_shadowing_repo_root(tmp_path)
    sentinel = _sentinel_module()
    # Prepend, so the entry sits at index 0: that is what makes a "restore by
    # appending to the tail" implementation observably wrong.
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])
    monkeypatch.setitem(sys.modules, "openalgo", sentinel)
    assert backtest_source._is_server_repo_entry(str(root))
    return root, sentinel


def test_pin_restores_sys_path_to_exact_original_list(monkeypatch, tmp_path):
    """The pin must hand back the identical `sys.path`, not an equivalent set.

    Exact list equality is the assertion that matters: an element-wise
    "is it in sys.path" check would pass even if the entry had been re-appended
    at the tail, which silently changes import priority for the rest of the
    process. The fake shadowing entry is prepended so a tail-append restore
    produces a visibly different list.
    """
    root, _ = _pin_with_shadowing(monkeypatch, tmp_path)
    path_before = list(sys.path)

    with backtest_source._pin_sdk_openalgo():
        # Inside the body the shadowing entry is gone and the SDK resolves --
        # otherwise the pin would not be doing the one job it exists for.
        assert str(root) not in sys.path
        assert hasattr(importlib.import_module("openalgo"), "ta")

    assert sys.path == path_before
    # Explicit position check, so a failure names the actual regression.
    assert sys.path.index(str(root)) == 0


def test_pin_restores_sys_modules_openalgo_by_identity(monkeypatch, tmp_path):
    """`sys.modules["openalgo"]` must come back as the same object it was.

    Identity, not equality. A `setdefault`-style restore is a silent no-op
    here, because the pin's own `import openalgo` has already claimed the key
    by the time the restore runs.
    """
    _, sentinel = _pin_with_shadowing(monkeypatch, tmp_path)
    assert sys.modules.get("openalgo") is sentinel

    with backtest_source._pin_sdk_openalgo():
        # The sentinel must actually have been displaced, otherwise this test
        # would pass without ever exercising the restore.
        assert sys.modules.get("openalgo") is not sentinel
        assert hasattr(importlib.import_module("openalgo"), "ta")

    assert sys.modules.get("openalgo") is sentinel


def test_pin_restores_state_even_when_body_raises(monkeypatch, tmp_path):
    """Global state is handed back on the failing path too.

    Covers the caller's `with` body raising.
    """
    _, sentinel = _pin_with_shadowing(monkeypatch, tmp_path)
    path_before = list(sys.path)

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with backtest_source._pin_sdk_openalgo():
            raise Boom

    assert sys.path == path_before
    assert sys.modules.get("openalgo") is sentinel


def test_pin_restores_state_when_a_mutation_fails_partway(monkeypatch, tmp_path):
    """Partial mutation failure must not leak stripped state.

    This is the case that actually distinguishes mutations inside the `try`
    from mutations outside it. With two shadowing entries, a `sys.path.remove`
    that fails on the second entry leaves the first one stripped. If the
    mutation loop sits inside the `try`, the `finally` puts it back; if it sits
    outside, the stripped entry escapes the failed call.

    (A failure raised by the pin's `import openalgo`, or by the caller's body,
    is handled by `finally` either way and so cannot observe this difference --
    which is why the obvious version of this test passed against the buggy
    shape.)
    """
    roots = [_seed_shadowing_repo_root(tmp_path / "a"), _seed_shadowing_repo_root(tmp_path / "b")]
    sentinel = _sentinel_module()

    class ExplodingPath(list):
        """sys.path that fails on the second removal, then behaves normally."""

        def __init__(self, values):
            super().__init__(values)
            self.removals = 0

        def remove(self, value):
            self.removals += 1
            if self.removals == 2:
                raise ValueError("simulated sys.path mutation failure")
            super().remove(value)

    path = ExplodingPath([str(roots[0]), str(roots[1]), *sys.path])
    monkeypatch.setattr(sys, "path", path)
    monkeypatch.setitem(sys.modules, "openalgo", sentinel)
    path_before = list(path)

    with pytest.raises(ValueError, match="simulated sys.path mutation failure"):
        with backtest_source._pin_sdk_openalgo():
            pytest.fail("body must not run when the pin fails")

    # Both shadowing entries are back, at their original positions.
    assert list(sys.path) == path_before


def test_pin_restores_state_when_sdk_import_fails(monkeypatch, tmp_path):
    """State is restored even when the pin's own SDK import fails.

    This is the case that distinguishes mutations-inside-the-`try` from
    mutations-outside-it. A failure raised by the caller's body is still
    handled by `finally` even when the mutations precede the `try`, so only a
    failure of the pin's *own* `import openalgo` can observe the difference:
    with the mutations outside the `try`, the stripped `sys.path` and the
    popped `sys.modules` entry would leak out of the failed call.
    """
    _, sentinel = _pin_with_shadowing(monkeypatch, tmp_path)
    path_before = list(sys.path)

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "openalgo":
            raise ImportError("simulated SDK import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)

    with pytest.raises(ImportError, match="simulated SDK import failure"):
        with backtest_source._pin_sdk_openalgo():
            pytest.fail("body must not run when the pin fails")

    assert sys.path == path_before
    assert sys.modules.get("openalgo") is sentinel


def test_loads_module_and_exposes_plan_for_day():
    m = backtest_source.load()
    assert hasattr(m, "plan_for_day")
    assert hasattr(m, "make_args")
    assert hasattr(m, "timeframe_bars")


def test_args_match_backtest_defaults():
    a = backtest_source.args()
    assert a.min_score == 65
    assert a.min_tf_score == 55
    assert a.confirm_rvol == 1.2
    assert a.max_stop_atr == 3.0
    assert a.horizon == 60
    assert a.warmup_days == 45


def test_load_is_cached():
    assert backtest_source.load() is backtest_source.load()


def test_missing_path_raises_named_error(monkeypatch, tmp_path):
    monkeypatch.setenv("INTRADAY_BACKTEST_PATH", str(tmp_path / "nope.py"))
    monkeypatch.setattr(backtest_source, "_CACHE", None)
    import importlib
    importlib.reload(backtest_source)
    try:
        with pytest.raises(FileNotFoundError, match="nope.py"):
            backtest_source.load()
    finally:
        monkeypatch.delenv("INTRADAY_BACKTEST_PATH")
        importlib.reload(backtest_source)


def test_loading_does_not_execute_main(tmp_path, monkeypatch):
    """`load()` must import the backtest, never run it.

    The backtest's `main()` is guarded by `if __name__ == "__main__"`, which is
    why the loader registers it under a synthetic name. Asserting only on
    `__name__` would be a proxy for the real guarantee, so this asserts the
    observable effect too: `main()` writes `reliance_backtest_trades.csv` and
    `reliance_backtest_summary.csv` into the CWD, so if it had run, those files
    would exist. Loading from a scratch CWD also keeps a regression from
    littering the repository.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backtest_source, "_CACHE", None)

    module = backtest_source.load()

    assert module.__name__ == backtest_source._MODULE_NAME
    assert module.__name__ != "__main__"
    assert callable(module.main)  # it exists -- it just must not have been called
    assert list(tmp_path.iterdir()) == [], "load() executed the backtest's main()"


def test_env_path_change_reloads_instead_of_returning_stale_cache(
    monkeypatch, tmp_path
):
    """A new INTRADAY_BACKTEST_PATH must not yield the previously cached module.

    Guards the cache-invalidation rule independently of the existing
    `setattr(_CACHE, None)` trick, so a stale module cannot be returned even if
    nothing pokes the cache global directly.
    """
    default_module = backtest_source.load()

    # A valid but different module file, so the reload must actually succeed.
    other = tmp_path / "other_backtest.py"
    other.write_text(
        "IST = 'Asia/Kolkata'\n"
        "def make_args():\n    return None\n"
        "def plan_for_day(*a, **k):\n    return None\n"
        "def timeframe_bars(*a, **k):\n    return None\n"
    )
    monkeypatch.setenv("INTRADAY_BACKTEST_PATH", str(other))
    try:
        reloaded = backtest_source.load()
        assert reloaded is not default_module, "stale module returned"
        assert reloaded.__name__ == backtest_source._MODULE_NAME
        assert reloaded.__file__ == str(other)
    finally:
        monkeypatch.delenv("INTRADAY_BACKTEST_PATH")

    # Back on the default path the real backtest is served again. The cache is
    # single-slot, so this re-imports rather than resurrecting the earlier
    # object; what matters is that the path is honoured, not object identity.
    restored = backtest_source.load()
    assert restored.__file__ == str(backtest_source.DEFAULT_PATH)
    assert restored.IST == default_module.IST
