"""Append-only decision journal for the intraday breakout bot.

One CSV per trading day, under `config.JOURNAL_DIR`, holding one row per
decision: plan-stage rejections, entries, exits, halts. `read_all()`
concatenates every file for the weekly scorer (Task 10).

Two things here are load-bearing:

* **`JOURNAL_DIR` is read as a module-level global at call time.** Tests
  monkeypatch `journal.JOURNAL_DIR` to a `tmp_path`; capturing it as a default
  argument or an import-time local would leave the tests writing to the real
  data directory instead. `path_for` reads the global on every call.
* **`JOURNAL_DIR` may not exist.** Task 1 deliberately removed the eager
  `mkdir` from `config` so that importing config creates only `DATA_DIR`.
  `path_for` therefore creates the parent directory before returning a path,
  so the very first `write` on a fresh install cannot fail on a missing dir.

The reason, stage and decision vocabularies are fixed and closed. A value
outside them is a bug in the caller, not a new category, so `write` raises
`ValueError` rather than widening a vocabulary silently -- a journal that
accepts arbitrary strings cannot be aggregated later, because Task 10 groups
by these columns and a single typo silently creates a phantom bucket that no
scorer ever reconciles. The reason vocabulary mixes SCREAMING_SNAKE tokens
(from `risk.py` and the exit paths) with one snake_case token,
`stop_too_wide`, because that is literally what `risk.py` emits; the asymmetry
is intentional and matching the producer is worth more than matching a style.

`risk.evaluate` returns `plan.error` verbatim as a rejection reason, which is
free text rather than a token. Task 7 must map that to `history_error` before
journalling; `write` will refuse anything else.
"""

from __future__ import annotations

import csv
import datetime
import logging
import os
import re
from pathlib import Path

import config
import pandas as pd

logger = logging.getLogger(__name__)

#: Module-level and deliberately rebindable: tests monkeypatch this to a
#: `tmp_path`. Read at call time, never captured into a default argument.
JOURNAL_DIR = config.JOURNAL_DIR

#: Column order. Downstream readers (Task 10) concatenate on these columns, so
#: the order is part of the interface, not a presentation detail.
FIELDS = [
    "ts",
    "symbol",
    "side",
    "stage",
    "decision",
    "reason",
    "plan_entry",
    "plan_stop",
    "plan_t1",
    "plan_t2",
    "qty",
    "order_id",
    "entry_price",
    "exit_price",
    "r_multiple",
    "pnl",
    "note",
]

#: The closed set of `reason` values. Anything else is rejected by `write`.
REASONS = frozenset(
    {
        # Rejections from risk.py.
        "WATCH_VOLUME",
        "stop_too_wide",
        "daily_loss_halt",
        "concurrency_cap",
        "margin_blocked",
        # Diagnostic bucket that plan.error free text is mapped into.
        "history_error",
        # Broker-side rejections.
        "order_rejected",
        # Exit reasons.
        "STOP",
        "T1_TRAIL",
        "T2",
        "EXPIRY",
        "SQUAREOFF",
    }
)

#: Suffix of the per-day file.
SUFFIX = ".csv"

#: The closed set of `stage` values -- which decision point produced the row.
#: Taken from the plan's global constraints. A typo here (`"Plan"`) would
#: otherwise land silently and become its own bucket in Task 10's groupby.
STAGES = frozenset(
    {"plan", "filter", "risk", "order", "fill", "exit", "squareoff"}
)

#: The closed set of `decision` values -- what actually happened.
DECISIONS = frozenset({"taken", "skipped", "filled", "exited"})

#: A journal file is named for its trading day and nothing else. Task 10's
#: weekly-changes CSV lives in the same directory, so `read_all` must not
#: sweep it in as phantom all-NaN decision rows.
DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv$")


def _now_iso() -> str:
    """Current instant as an IST ISO-8601 string.

    ISO-8601 (not a locale-formatted string) because these files are parsed
    back by `pd.read_csv` and by the weekly scorer.
    """
    return datetime.datetime.now(tz=config.TZ).isoformat()


def path_for(day: datetime.date) -> Path:
    """Path of `day`'s journal file, with the parent directory created.

    The `mkdir` is what makes this safe to call on a fresh install: `config`
    no longer creates `JOURNAL_DIR`, so the first caller of the day pays for
    it. `exist_ok=True` keeps it free on every subsequent call.
    """
    directory = Path(JOURNAL_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{day.isoformat()}{SUFFIX}"


def validate_reason(reason: str) -> bool:
    """True if `reason` is in the fixed vocabulary."""
    return reason in REASONS


def validate_stage(stage: str) -> bool:
    """True if `stage` is in the fixed vocabulary."""
    return stage in STAGES


def validate_decision(decision: str) -> bool:
    """True if `decision` is in the fixed vocabulary."""
    return decision in DECISIONS


def _plan_fields(plan) -> tuple[object, object, object, object]:
    """Pull the four plan levels out of a `Plan`, tolerating a partial one.

    Uses `getattr` with a `None` default rather than direct attribute access,
    and that is deliberate: the journal's job is to *record* what it was
    handed, on a path where a raised exception would abort an entry loop over
    one malformed plan. The cost of a renamed `Plan` field is that the cell
    lands as NaN in the CSV rather than raising at the call site -- visible
    later in the weekly scorer as a missing column value, which is recoverable,
    whereas an exception here would be a crash with the cause one frame away
    in the caller's code. `planner.Plan`'s own docstring pins these field names
    as load-bearing for Tasks 4/7/9, so a rename should break loudly there
    first.
    """
    return (
        getattr(plan, "entry", None),
        getattr(plan, "stop", None),
        getattr(plan, "target1", None),
        getattr(plan, "target2", None),
    )


def write(
    day: datetime.date,
    symbol: str,
    side: str,
    stage: str,
    decision: str,
    reason: str | None = None,
    plan=None,
    qty: int = 0,
    order_id: str = "",
    entry_price: float | None = None,
    exit_price: float | None = None,
    r_multiple: float | None = None,
    pnl: float | None = None,
    note: str = "",
) -> None:
    """Append one decision row to `day`'s journal, creating it with a header.

    Raises `ValueError` if a truthy `reason`, or the `stage`/`decision`, is
    outside its fixed vocabulary. `reason=None` and `reason=""` are both "no
    reason" and are written as an empty cell, so a file never mixes a null
    token with an empty string.

    **Durability.** The handle is flushed and `fsync`ed before returning, so a
    row that `write` returned for is on disk rather than in a buffer that a
    kill -9 would discard. It is still not *atomic*: an interrupt mid-write
    can leave a truncated final line, which pandas reads as NaN rather than
    failing. That is the deliberate trade -- a torn last row costs one bad
    row in the weekly scorer, whereas the alternative (write-temp-then-rename)
    would replace the file on every append and make a concurrent reader see a
    half-written day. The worst form of a torn file -- a zero-byte one, from an
    interrupt between the append-open and the header-write -- is handled by
    `_read`, which degrades it to the empty frame rather than raising.
    """
    if reason and not validate_reason(reason):
        raise ValueError(
            f"reason {reason!r} is not in the journal vocabulary "
            f"{sorted(REASONS)}"
        )
    if not validate_stage(stage):
        raise ValueError(
            f"stage {stage!r} is not in the journal vocabulary {sorted(STAGES)}"
        )
    if not validate_decision(decision):
        raise ValueError(
            f"decision {decision!r} is not in the journal vocabulary "
            f"{sorted(DECISIONS)}"
        )

    if plan is not None:
        plan_entry, plan_stop, plan_t1, plan_t2 = _plan_fields(plan)
    else:
        plan_entry = plan_stop = plan_t1 = plan_t2 = None

    row = {
        "ts": _now_iso(),
        "symbol": symbol,
        "side": side,
        "stage": stage,
        "decision": decision,
        "reason": reason or "",
        "plan_entry": plan_entry,
        "plan_stop": plan_stop,
        "plan_t1": plan_t1,
        "plan_t2": plan_t2,
        "qty": qty,
        "order_id": order_id,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "r_multiple": r_multiple,
        "pnl": pnl,
        "note": note,
    }

    path = path_for(day)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
        # See the durability note in the docstring: a row this call returned
        # for must survive a crash, not sit in a buffer.
        handle.flush()
        os.fsync(handle.fileno())


def _read(path: Path) -> pd.DataFrame:
    """Read one journal file, re-asserting `FIELDS` as the column order.

    Any file that cannot be parsed degrades to the empty `FIELDS` frame rather
    than raising. This is not defensive padding -- it is reachable from
    `write` itself, which opens the file in append mode (creating a zero-byte
    file) *before* writing the header. An interrupt in that window leaves a
    zero-byte file, and `pd.read_csv` on zero bytes raises `EmptyDataError`.
    A torn final line raises `ParserError`. Either one would otherwise make
    every subsequent read of that day raise forever, and because `read_all`
    has no per-file recovery, one such file takes down the whole Task 10
    weekly scorer for the remaining life of the journal.

    A bad file costs at most the rows in that one file; the trade is
    deliberate, because the alternative -- propagating -- costs the caller
    every other day's data too. The warning names the path so the loss is
    diagnosable rather than silent.
    """
    try:
        frame = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        logger.warning("unreadable journal file %s (%s); treating as empty", path, exc)
        return _empty()
    return frame.reindex(columns=FIELDS)


def _empty() -> pd.DataFrame:
    """The canonical empty journal: zero rows, `FIELDS` columns."""
    return pd.DataFrame(columns=FIELDS)


def read_day(day: datetime.date) -> pd.DataFrame:
    """Every row journalled for `day`.

    A day with no file yields the empty `FIELDS` frame rather than raising:
    "nothing happened" is the normal state of a day the bot did not trade.
    """
    path = Path(JOURNAL_DIR) / f"{day.isoformat()}{SUFFIX}"
    if not path.exists():
        return _empty()
    return _read(path)


def _journal_files(directory: Path) -> list[Path]:
    """Day files under `directory`, oldest first.

    Only strictly `YYYY-MM-DD.csv` names qualify. Task 10's weekly-changes
    CSV shares this directory, and an unfiltered `*.csv` glob would read it
    as phantom all-NaN decision rows that the scorer then double-counts.
    Subdirectories matching the pattern are skipped.

    Ordering is a lexicographic sort of the filename, which is chronological
    only because `YYYY-MM-DD` is fixed-width and zero-padded -- that is
    load-bearing now that the name filter is in place, so changing the naming
    scheme means changing this sort.
    """
    return sorted(
        path
        for path in directory.glob(f"*{SUFFIX}")
        if path.is_file() and DAY_FILE.match(path.name)
    )


def read_all() -> pd.DataFrame:
    """Every row from every journal file, oldest day first.

    Returns the empty `FIELDS` frame when the directory is missing or holds no
    day files. Task 10 concatenates this against other frames, so the columns
    must be present even with no data -- a bare empty frame would fail to
    concat.
    """
    directory = Path(JOURNAL_DIR)
    if not directory.is_dir():
        return _empty()

    frames = [_read(path) for path in _journal_files(directory)]
    if not frames:
        return _empty()

    return pd.concat(frames, ignore_index=True).reindex(columns=FIELDS)


__all__ = [
    "DECISIONS",
    "FIELDS",
    "JOURNAL_DIR",
    "REASONS",
    "STAGES",
    "SUFFIX",
    "path_for",
    "read_all",
    "read_day",
    "validate_decision",
    "validate_reason",
    "validate_stage",
    "write",
]
