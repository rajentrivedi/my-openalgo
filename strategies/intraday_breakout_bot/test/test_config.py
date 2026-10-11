import os
import importlib
import pytest


def reload_config(**env):
    # Deliberately does NOT pop anything: monkeypatch owns the environment
    # (setenv/delenv) and restores it after each test. Passing env keys here
    # just overrides on top for this reload.
    os.environ.update(env)
    import config
    return importlib.reload(config)


def test_dry_run_defaults_true(monkeypatch):
    monkeypatch.setenv("OPENALGO_API_KEY", "x" * 64)
    monkeypatch.delenv("OPENALGO_DRY_RUN", raising=False)
    c = reload_config()
    assert c.DRY_RUN is True


def test_dry_run_can_be_disabled(monkeypatch):
    monkeypatch.setenv("OPENALGO_API_KEY", "x" * 64)
    monkeypatch.setenv("OPENALGO_DRY_RUN", "0")
    c = reload_config()
    assert c.DRY_RUN is False


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENALGO_API_KEY"):
        reload_config()


def test_budget_and_risk_constants():
    import config
    assert config.BUDGET == 100000.0
    assert config.RISK_PCT == 1.0
    assert config.MAX_CONCURRENT == 4
    assert config.DAILY_LOSS_LIMIT == 3000.0


def test_data_dir_is_created():
    import config
    assert config.DATA_DIR.is_dir()
    assert config.UNIVERSE_PATH.parent == config.DATA_DIR
