# test/test_journal.py
import datetime as dt

import journal
import pandas as pd


def test_write_creates_file_with_header(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    day = dt.date(2026, 10, 8)
    journal.write(day, "TCS", "LONG", "plan", "taken", "WATCH_VOLUME")
    p = journal.path_for(day)
    assert p.exists()
    df = pd.read_csv(p)
    assert list(df.columns) == journal.FIELDS
    assert len(df) == 1


def test_write_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    day = dt.date(2026, 10, 8)
    journal.write(day, "TCS", "LONG", "plan", "taken")
    journal.write(day, "INFY", "SHORT", "plan", "taken")
    assert len(journal.read_day(day)) == 2


def test_read_all_concatenates_days(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    journal.write(dt.date(2026, 10, 8), "TCS", "LONG", "plan", "taken")
    journal.write(dt.date(2026, 10, 9), "INFY", "SHORT", "plan", "taken")
    assert len(journal.read_all()) == 2


def test_reason_vocabulary_is_enforced():
    assert journal.validate_reason("WATCH_VOLUME")
    assert journal.validate_reason("T1_TRAIL")
    assert journal.validate_reason("SQUAREOFF")
    assert not journal.validate_reason("made_up_reason")


def test_unknown_reason_raises(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    with pytest.raises(ValueError, match="reason"):
        journal.write(dt.date(2026, 10, 8), "TCS", "LONG", "plan",
                      "taken", reason="totally_invalid")
