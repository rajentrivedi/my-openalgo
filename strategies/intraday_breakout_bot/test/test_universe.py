# test/test_universe.py
import json

import universe


def test_thresholds_match_the_screen():
    assert universe.THRESHOLDS == {
        "min_trades": 30, "min_total_r": 8.0,
        "min_win_rate": 50.0, "min_pf": 1.5,
    }


def test_passes_requires_all_four():
    good = {"trades": 40, "total_r": 10, "win_rate_pct": 55, "pf": 2.0}
    assert universe.passes(good)
    assert not universe.passes({**good, "trades": 29})
    assert not universe.passes({**good, "total_r": 7.9})
    assert not universe.passes({**good, "win_rate_pct": 49.9})
    assert not universe.passes({**good, "pf": 1.49})


def test_shipped_universe_has_64_symbols():
    symbols = universe.load()
    assert len(symbols) == 64
    assert "TCS" in symbols
    assert "KAYNES" in symbols


def test_missing_universe_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "UNIVERSE_PATH", tmp_path / "missing.json")
    import pytest
    with pytest.raises(RuntimeError, match="universe"):
        universe.load()


def test_malformed_universe_raises(tmp_path, monkeypatch):
    p = tmp_path / "universe.json"
    p.write_text('{"symbols": "not-a-list"}')
    monkeypatch.setattr(universe, "UNIVERSE_PATH", p)
    import pytest
    with pytest.raises(RuntimeError):
        universe.load()
