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


# --- empty-shape contract (Task 10 concatenates on these columns) ------------

def test_read_all_on_missing_dir_has_exact_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "does_not_exist")
    df = journal.read_all()
    assert list(df.columns) == journal.FIELDS
    assert len(df) == 0


def test_read_all_on_empty_dir_has_exact_fields(tmp_path, monkeypatch):
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    df = journal.read_all()
    assert list(df.columns) == journal.FIELDS
    assert len(df) == 0


def test_read_day_on_missing_day_has_exact_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    df = journal.read_day(dt.date(2026, 10, 8))
    assert list(df.columns) == journal.FIELDS
    assert len(df) == 0


# --- Critical 2: a zero-byte file must not poison every future read ---------

def test_zero_byte_file_reads_as_empty_with_fields(tmp_path, monkeypatch):
    """Reachable from `write` itself: it opens the file (creating it) before
    writing the header, so an interrupt in that window leaves zero bytes."""
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    (tmp_path / "2026-10-08.csv").write_text("")
    assert list(journal.read_day(dt.date(2026, 10, 8)).columns) == journal.FIELDS


def test_zero_byte_file_does_not_break_read_all(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    journal.write(dt.date(2026, 10, 9), "INFY", "SHORT", "plan", "taken")
    (tmp_path / "2026-10-08.csv").write_text("")  # earlier day, sorts first
    df = journal.read_all()
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "INFY"


def test_header_only_file_reads_as_empty_with_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    path = tmp_path / "2026-10-08.csv"
    path.write_text(",".join(journal.FIELDS) + "\n")
    df = journal.read_day(dt.date(2026, 10, 8))
    assert list(df.columns) == journal.FIELDS
    assert len(df) == 0


def test_garbage_file_does_not_break_read_all(tmp_path, monkeypatch):
    """A truncated first line must not take down the whole scorer."""
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    journal.write(dt.date(2026, 10, 9), "INFY", "SHORT", "plan", "taken")
    (tmp_path / "2026-10-08.csv").write_text('"ts,symbol,side\n2026-10-08T0')
    df = journal.read_all()
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "INFY"


# --- Important 3: only date-named files are journal files -------------------

def test_read_all_ignores_non_date_named_csv(tmp_path, monkeypatch):
    """Task 10's weekly changes CSV must not be read as decision rows."""
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    journal.write(dt.date(2026, 10, 8), "TCS", "LONG", "plan", "taken")
    (tmp_path / "weekly_changes.csv").write_text(
        "ts,symbol,delta\n2026-10-08,TCS,10\n"
    )
    df = journal.read_all()
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "TCS"


def test_read_all_ignores_misc_files(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    journal.write(dt.date(2026, 10, 8), "TCS", "LONG", "plan", "taken")
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "2026-13-99.csv").write_text("")   # date-shaped but impossible
    assert len(journal.read_all()) == 1


# --- Important 7: stage/decision are closed vocabularies too ---------------

def test_stage_and_decision_vocabularies():
    assert journal.validate_stage("plan")
    assert journal.validate_stage("squareoff")
    assert not journal.validate_stage("Plan")
    assert journal.validate_decision("taken")
    assert journal.validate_decision("exited")
    assert not journal.validate_decision("Taken")


def test_unknown_stage_raises(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    with pytest.raises(ValueError, match="stage"):
        journal.write(dt.date(2026, 10, 8), "TCS", "LONG", "Plan", "taken")


def test_unknown_decision_raises(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    with pytest.raises(ValueError, match="decision"):
        journal.write(dt.date(2026, 10, 8), "TCS", "LONG", "plan", "Took")


# --- crash-window header bug: a zero-byte file must not eat the next row ----

def test_write_after_zero_byte_file_keeps_the_row(tmp_path, monkeypatch):
    """Regression: a leftover zero-byte file made `write` skip the header, so
    the data row landed on line 1 and `read_csv` parsed it AS the header.
    The day's first real decision was silently lost."""
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    (tmp_path / "2026-10-11.csv").write_text("")  # interrupt mid-`write`
    journal.write(dt.date(2026, 10, 11), "TCS", "LONG", "plan", "taken")
    df = journal.read_day(dt.date(2026, 10, 11))
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "TCS"
    assert df.iloc[0]["decision"] == "taken"


def test_zero_byte_file_then_two_writes_keeps_both(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    (tmp_path / "2026-10-11.csv").write_text("")
    journal.write(dt.date(2026, 10, 11), "TCS", "LONG", "plan", "taken")
    journal.write(dt.date(2026, 10, 11), "INFY", "SHORT", "plan", "taken")
    df = journal.read_day(dt.date(2026, 10, 11))
    assert len(df) == 2
    assert list(df["symbol"]) == ["TCS", "INFY"]


def test_zero_byte_file_read_alone_still_empty(tmp_path, monkeypatch):
    """Round 1's C2 fix must not regress: read alone still degrades."""
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    (tmp_path / "2026-10-11.csv").write_text("")
    df = journal.read_day(dt.date(2026, 10, 11))
    assert list(df.columns) == journal.FIELDS
    assert len(df) == 0


def test_write_recovers_file_whose_first_line_is_a_data_row(tmp_path, monkeypatch):
    """A file already in the corrupted state (data row as line 1) must not
    lose the data that is already in it, nor the row being written now."""
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    path = tmp_path / "2026-10-11.csv"
    path.write_text(
        "2026-10-11T09:20:53+05:30,TCS,LONG,plan,taken,,,,,,0,,,,,\n"
    )
    journal.write(dt.date(2026, 10, 11), "INFY", "SHORT", "plan", "taken")
    df = journal.read_day(dt.date(2026, 10, 11))
    assert len(df) == 2
    assert list(df["symbol"]) == ["TCS", "INFY"]
