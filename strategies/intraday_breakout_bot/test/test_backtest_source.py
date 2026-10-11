import backtest_source
import pytest


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
