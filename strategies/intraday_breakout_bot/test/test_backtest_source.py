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