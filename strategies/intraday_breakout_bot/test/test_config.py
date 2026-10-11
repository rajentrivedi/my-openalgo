import importlib
import pytest


def reload_config():
    """Re-execute config.py against the current process environment.

    Takes no arguments on purpose: this helper has no way to mutate
    os.environ, so it cannot leak state into later tests. Tests that need a
    different environment must use monkeypatch.setenv/delenv, which pytest
    restores automatically at teardown.
    """
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


# --- assert_paper_only() ----------------------------------------------------
#
# This is the safety gate that must keep the bot off a live account, so every
# branch is exercised with an injected fake SDK. No test here touches the
# network: config._sdk_factory is swapped for a stub.


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def funds(self):
        self.calls += 1
        return self._response


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install a fake SDK factory and return a recorder for its arguments."""

    import config

    created = {}

    def factory(api_key, host):
        created["api_key"] = api_key
        created["host"] = host
        created["client"] = _FakeClient(config._FAKE_RESPONSE)
        return created["client"]

    monkeypatch.setattr(config, "_FAKE_RESPONSE", {"mode": "analyze"}, raising=False)
    monkeypatch.setattr(config, "_sdk_factory", factory)
    return created


def test_assert_paper_only_passes_in_analyze_mode(fake_sdk):
    import config

    config.assert_paper_only()  # must not raise

    assert fake_sdk["client"].calls == 1


def test_assert_paper_only_uses_configured_credentials(fake_sdk):
    import config

    config.assert_paper_only()

    assert fake_sdk["api_key"] == config.API_KEY
    assert fake_sdk["host"] == config.API_HOST


@pytest.mark.parametrize("bad_mode", ["live", "LIVE", "", None])
def test_assert_paper_only_raises_when_not_analyze(fake_sdk, monkeypatch, bad_mode):
    import config

    monkeypatch.setattr(config, "_FAKE_RESPONSE", {"mode": bad_mode})

    with pytest.raises(RuntimeError, match="analyze"):
        config.assert_paper_only()


@pytest.mark.parametrize(
    "bad_response",
    [None, [], "analyze", 1, object()],
    ids=["none", "list", "str", "int", "object"],
)
def test_assert_paper_only_raises_on_malformed_response(
    fake_sdk, monkeypatch, bad_response
):
    """A non-dict response must raise RuntimeError, not AttributeError."""
    import config

    monkeypatch.setattr(config, "_FAKE_RESPONSE", bad_response)

    with pytest.raises(RuntimeError, match="expected a dict"):
        config.assert_paper_only()


def test_assert_paper_only_raises_when_mode_key_missing(fake_sdk, monkeypatch):
    import config

    monkeypatch.setattr(config, "_FAKE_RESPONSE", {"availablemargin": 1.0})

    with pytest.raises(RuntimeError, match="analyze"):
        config.assert_paper_only()


def test_api_key_not_exported_via_star_import(monkeypatch):
    """API_KEY stays importable by name but is not re-exported by `*`."""
    monkeypatch.setenv("OPENALGO_API_KEY", "x" * 64)
    c = reload_config()

    assert "API_KEY" not in c.__all__
    assert c.API_KEY  # still available to modules that need it explicitly
