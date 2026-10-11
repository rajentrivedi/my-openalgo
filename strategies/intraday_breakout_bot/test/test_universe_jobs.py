# test/test_universe_jobs.py
import universe_weekly


def test_weekly_drops_failing_and_promotes(tmp_path, monkeypatch):
    monkeypatch.setattr(universe_weekly.universe, "UNIVERSE_PATH", tmp_path / "u.json")
    monkeypatch.setattr(universe_weekly, "CANDIDATES", tmp_path / "c.csv")
    import pandas as pd
    universe_weekly.universe.save(["GOOD", "BAD"])
    pd.DataFrame([
        {"symbol": "GOOD", "trades": 40, "total_r": 12, "win_rate_pct": 56, "pf": 2.0},
        {"symbol": "BAD", "trades": 5, "total_r": -1, "win_rate_pct": 30, "pf": 0.5},
        {"symbol": "NEW", "trades": 50, "total_r": 11, "win_rate_pct": 55, "pf": 1.9},
    ]).to_csv(tmp_path / "c.csv", index=False)

    result = universe_weekly.run(target_size=2)
    assert result["kept"] == ["GOOD"]
    assert result["dropped"] == ["BAD"]
    assert result["promoted"] == ["NEW"]
    assert universe_weekly.universe.load() == ["NEW", "GOOD"]
