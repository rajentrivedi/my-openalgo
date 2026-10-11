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

The reason vocabulary is fixed and closed. A reason that is not in it is a
bug in the caller, not a new category, so `write` raises `ValueError` rather
than widening the vocabulary silently -- a journal that accepts arbitrary
reason strings cannot be aggregated by reason later. The vocabulary mixes
SCREAMING_SNAKE tokens (from `risk.py` and the exit paths) with one snake_case
token, `stop_too_wide`, because that is literally what `risk.py` emits; the
asymmetry is intentional and matching the producer is worth more than
matching a style.

`risk.evaluate` returns `plan.error` verbatim as a rejection reason, which is
free text rather than a token. Task 7 must map that to `history_error` before
journalling; `write` will refuse anything else.
"""

from __future__ import annotations

import csv
import datetime
import logging
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


def _plan_fields(plan) -> tuple[object, object, object, object]:
    """Pull the four plan levels out of a `Plan`, tolerating a partial one.

    Uses `getattr` so a plain object or a stub plan works as well as the real
    dataclass -- the journal records what it was told, and must not be the
    component that fails when a caller passes something slightly different.
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

    Raises `ValueError` if `reason` is truthy but outside the fixed
    vocabulary. `None` and `""` are both "no reason" and are written as an
    empty cell, so a file never mixes a null token with an empty string.
    """
    if reason and not validate_reason(reason):
        raise ValueError(
            f"reason {reason!r} is not in the journal vocabulary "
            f"{sorted(REASONS)}"
        )

    plan_entry, plan_stop, plan_t1, plan_t2 = _plan_fields(plan) if plan is not None else (None, None, None, None)

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


def _read(path: Path) -> pd.DataFrame:
    """Read one journal file, re-asserting `FIELDS` as the column order.

    An empty (header-only) file is a real possibility -- `write` creates the
    header before the first row and a crash can land in between -- and
    `pd.read_csv` on it returns a frame with no columns at all. Re-indexing to
    `FIELDS` means every reader sees the same shape whether the file is
    missing, empty, or populated.
    """
    frame = pd.read_csv(path)
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


def read_all() -> pd.DataFrame:
    """Every row from every journal file, oldest day first.

    Returns the empty `FIELDS` frame when the directory is missing or holds no
    files. Task 10 concatenates this against other frames, so the columns must
    be present even with no data -- a bare empty frame would fail to concat.
    """
    directory = Path(JOURNAL_DIR)
    if not directory.is_dir():
        return _empty()

    frames = [
        _read(path)
        for path in sorted(directory.glob(f"*{SUFFIX}"))
        if path.is_file()
    ]
    if not frames:
        return _empty()

    return pd.concat(frames, ignore_index=True).reindex(columns=FIELDS)


__all__ = [
    "FIELDS",
    "JOURNAL_DIR",
    "REASONS",
    "SUFFIX",
    "path_for",
    "read_all",
    "read_day",
    "validate_reason",
    "write",
]
