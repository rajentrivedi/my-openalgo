# test/test_universe_jobs.py
import csv
import subprocess

import universe_monthly
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


def test_weekly_promotes_by_rank_and_truncates_to_target(tmp_path, monkeypatch):
    """More passers than free slots: rank order and target_size are exact.

    One kept symbol and three promotable candidates of differing TotalR, with
    only two free slots (target_size=3). The weekly job must promote the two
    highest-ranked candidates, drop the third on the truncation boundary, and
    write promoted-first-then-kept order. This pins both the promotion ordering
    and the ``>=`` truncation against an off-by-one.
    """
    monkeypatch.setattr(universe_weekly.universe, "UNIVERSE_PATH", tmp_path / "u.json")
    monkeypatch.setattr(universe_weekly, "CANDIDATES", tmp_path / "c.csv")
    import pandas as pd

    universe_weekly.universe.save(["KEEP"])
    # candidates.csv ships sorted by TotalR descending (that is the promotion
    # order the weekly job relies on): PROM1 > PROM2 > KEEP > PROM3.
    pd.DataFrame([
        {"symbol": "PROM1", "trades": 50, "total_r": 20, "win_rate_pct": 60, "pf": 2.5},
        {"symbol": "PROM2", "trades": 45, "total_r": 15, "win_rate_pct": 58, "pf": 2.2},
        {"symbol": "KEEP", "trades": 40, "total_r": 12, "win_rate_pct": 56, "pf": 2.0},
        {"symbol": "PROM3", "trades": 35, "total_r": 10, "win_rate_pct": 52, "pf": 1.8},
    ]).to_csv(tmp_path / "c.csv", index=False)

    result = universe_weekly.run(target_size=3)

    assert result["kept"] == ["KEEP"]
    assert result["dropped"] == []
    # (a) highest-ranked first, and (b) exactly two promoted -- PROM3 is the
    # one past the truncation boundary and must NOT appear.
    assert result["promoted"] == ["PROM1", "PROM2"]
    assert "PROM3" not in result["promoted"]
    # (c) promoted-first-then-kept, and the book is truncated to exactly
    # target_size (an off-by-one in the >= guard would admit PROM3 -> len 4).
    final = universe_weekly.universe.load()
    assert final == ["PROM1", "PROM2", "KEEP"]
    assert len(final) == 3


def test_monthly_rebuilds_candidates_from_fresh_report_without_touching_universe(
    tmp_path, monkeypatch
):
    """Monthly run must rebuild candidates.csv from the FRESH batch report and
    leave universe.json untouched.

    The batch subprocess is faked: it writes a ``batch_report.csv`` into the
    forced out-dir using the lowercase ``batch_report`` schema and carrying a
    symbol/metric that does NOT exist in the static ``N500_REPORT.csv``. After
    ``run()``:

    (a) candidates.csv reflects the fresh data -- the fresh-only symbol appears,
        in the canonical ``Symbol,Trades,TotalR,Win%,PF`` schema, sorted by
        TotalR descending; and
    (b) universe.json is byte-for-byte unchanged, proving the monthly job can no
        longer reset the live book.

    Hermetic: out-dir and candidates.csv are redirected into tmp_path, and the
    generator's ``UNIVERSE_JSON`` is pointed at a tmp universe.json seeded with
    a book that differs from the static 64-symbol screen (as the weekly job
    would have left it), so any accidental universe write lands in tmp and is
    caught rather than corrupting the shipped file.
    """
    out_dir = tmp_path / "batch_out"
    candidates = tmp_path / "candidates.csv"
    seeded_universe = tmp_path / "universe.json"
    # A promoted/dropped book that is NOT the static screen's 64 symbols.
    seeded_universe.write_text(
        '{"generated": "2026-11-01", "note": "weekly refresh", '
        '"symbols": ["WEEKLY1", "WEEKLY2"]}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(universe_monthly, "OUT_DIR", out_dir, raising=False)
    monkeypatch.setattr(universe_monthly, "CANDIDATES_CSV", candidates, raising=False)

    real_loader = universe_monthly._load_generator

    def _redirected_loader():
        gen = real_loader()
        # Belt and suspenders: if a regression made the rebuild rewrite the
        # universe, it would hit tmp (and fail the assertion) rather than the
        # shipped data/universe.json.
        gen.UNIVERSE_JSON = seeded_universe
        gen.CANDIDATES_CSV = candidates
        return gen

    monkeypatch.setattr(
        universe_monthly, "_load_generator", _redirected_loader, raising=False
    )

    def fake_batch(cmd, *args, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        # Fresh batch_report.csv: lowercase schema, a fresh-only symbol
        # (FRESHCO) with the top TotalR, and TCS with metrics different from
        # the static N500 source. None of this exists in N500_REPORT.csv.
        (out_dir / "batch_report.csv").write_text(
            "symbol,status,trades,win_rate_pct,total_r,profit_factor\n"
            "FRESHCO,ok,40,61.0,99.5,3.2\n"
            "TCS,ok,49,65.31,15.517,3.012\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(universe_monthly.subprocess, "run", fake_batch)

    before = seeded_universe.read_text(encoding="utf-8")
    rc = universe_monthly.run()
    after = seeded_universe.read_text(encoding="utf-8")

    assert rc == 0

    with candidates.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # (a) canonical schema, fresh data, sorted by TotalR descending.
    assert list(rows[0].keys()) == ["Symbol", "Trades", "TotalR", "Win%", "PF"]
    assert [r["Symbol"] for r in rows] == ["FRESHCO", "TCS"]
    assert rows[0]["TotalR"] == "99.5"
    assert rows[0]["Win%"] == "61.0"
    assert rows[0]["PF"] == "3.2"
    # The fresh-only symbol must be present -- proves the stale static read is
    # gone (FRESHCO is absent from N500_REPORT.csv).
    assert any(r["Symbol"] == "FRESHCO" for r in rows)

    # (b) universe.json untouched by the monthly run.
    assert after == before
