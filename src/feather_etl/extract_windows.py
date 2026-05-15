"""Window enumeration for batched-append extracts (#62, #63).

Supports three strategies:
  single    — one window covering the full table (degenerate case, default)
  date      — one window per day over a datetime range
  pk_range  — N equal-width integer chunks over an integer primary-key range

SQL safety
----------
All identifier composition routes through `_quote_for_dialect`.  Before any
identifier is interpolated into SQL, it MUST pass `_IDENTIFIER_RE`; otherwise
`ValueError` is raised.  Values (timestamps, integers) are rendered as
literals from Python objects — never from raw user strings.

WindowSpec.source_filter
------------------------
Holds the per-window WHERE fragment for the **source** fetch (same predicate
shape as `window_predicate` but in the source system's quoting convention).
`None` means "fetch the whole table" — used for the single-window case.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from feather_etl.config import TableConfig

# ---------------------------------------------------------------------------
# Identifier safety
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][\w\s]*$")

_DIALECT_QUOTE: dict[str, tuple[str, str]] = {
    "destination": ('"', '"'),
    "postgres": ('"', '"'),
    "sqlserver": ("[", "]"),
    "mysql": ("`", "`"),
}


def _quote_for_dialect(column: str, dialect: str) -> str:
    """Return *column* wrapped in the identifier-quoting chars for *dialect*.

    Raises ``ValueError`` if *column* contains characters outside the safe
    identifier pattern (injection guard) or if *dialect* is unrecognised.
    """
    if not _IDENTIFIER_RE.match(column):
        raise ValueError(
            f"Unsafe column name {column!r}: must match ^[A-Za-z_][\\w\\s]*$ "
            "(reject to prevent SQL injection)"
        )
    try:
        open_q, close_q = _DIALECT_QUOTE[dialect]
    except KeyError:
        supported = ", ".join(sorted(_DIALECT_QUOTE))
        raise ValueError(
            f"Unknown dialect {dialect!r}; supported: {supported}"
        ) from None
    return f"{open_q}{column}{close_q}"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowSpec:
    """One unit of resumable work for a table extract.

    ``window_predicate`` is interpolated directly into a DELETE statement on
    the bronze table — it MUST be safe SQL composed by the planner, never
    raw user input.  The single-window case uses ``1=1`` (delete everything).

    ``source_filter`` is the same WHERE clause expressed in the source
    system's quoting convention.  When ``None``, callers should not push any
    filter into the source fetch — used for the single-window (full-table)
    case.

    All SQL composition routes through ``_quote_for_dialect``; identifier
    safety is enforced before anything is interpolated.
    """

    window_key: str
    window_predicate: str
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    source_filter: Optional[str] = None


@dataclass
class WindowResult:
    """Per-window outcome returned by destination.load_batched_append.

    Only constructed for committed windows.  Failures raise the underlying
    exception from within ``load_batched_append`` after a transaction
    rollback.
    """

    window_key: str
    rows_loaded: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _date_predicate(column: str, start: datetime, end: datetime, dialect: str) -> str:
    """Build a half-open [start, end) datetime predicate in *dialect* quoting."""
    quoted = _quote_for_dialect(column, dialect)
    s = start.isoformat()
    e = end.isoformat()
    return f"{quoted} >= '{s}' AND {quoted} < '{e}'"


def _int_predicate(column: str, lo: int, hi_exclusive: int, dialect: str) -> str:
    """Build a half-open [lo, hi_exclusive) integer predicate in *dialect* quoting."""
    quoted = _quote_for_dialect(column, dialect)
    return f"{quoted} >= {lo} AND {quoted} < {hi_exclusive}"


def _plan_date_windows(
    column: str,
    grain: str,
    range_: Tuple[datetime, datetime],
    source_dialect: str,
) -> list[WindowSpec]:
    """Enumerate one WindowSpec per *grain* slice over *range_*."""
    if grain != "1d":
        raise ValueError(
            f"Unsupported grain {grain!r}; only '1d' is supported "
            "(follow-up issue will add finer grains)"
        )
    delta = timedelta(days=1)

    min_dt, max_dt = range_
    # Normalise to the start of the day containing min_dt
    start = datetime(min_dt.year, min_dt.month, min_dt.day, 0, 0, 0)

    windows: list[WindowSpec] = []
    while start <= max_dt:
        end = start + delta
        key = start.date().isoformat()
        dest_pred = _date_predicate(column, start, end, "destination")
        src_filter = _date_predicate(column, start, end, source_dialect)
        windows.append(
            WindowSpec(
                window_key=key,
                window_predicate=dest_pred,
                window_start=start,
                window_end=end,
                source_filter=src_filter,
            )
        )
        start = end

    return windows


def _plan_pk_range_windows(
    column: str,
    range_: Tuple[int, int],
    source_dialect: str,
) -> list[WindowSpec]:
    """Divide *range_* into N equal-width integer chunks."""
    min_int, max_int = range_
    total = max_int - min_int + 1

    if total <= 0:
        raise ValueError(
            f"pk_range: min ({min_int}) must be <= max ({max_int})"
        )

    # When total is very small, emit one window per value (up to 8 max via
    # the formula); the formula naturally handles this: total // 100_000 = 0,
    # so max(8, 0) = 8, min(64, 8) = 8.  But if total < 8 we want exactly
    # *total* windows.
    n = min(64, max(8, total // 100_000))
    if total < n:
        n = total

    chunk = math.ceil(total / n)

    windows: list[WindowSpec] = []
    lo = min_int
    for i in range(n):
        hi_exclusive = lo + chunk
        # Last window: clamp to max_int + 1 to ensure max_int is covered.
        if i == n - 1:
            hi_exclusive = max_int + 1
        key = f"{lo}-{hi_exclusive - 1}"
        dest_pred = _int_predicate(column, lo, hi_exclusive, "destination")
        src_filter = _int_predicate(column, lo, hi_exclusive, source_dialect)
        windows.append(
            WindowSpec(
                window_key=key,
                window_predicate=dest_pred,
                window_start=None,
                window_end=None,
                source_filter=src_filter,
            )
        )
        lo = hi_exclusive
        if lo > max_int:
            break

    return windows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_windows(
    table: Optional[TableConfig] = None,
    *,
    strategy: str = "single",
    column: Optional[str] = None,
    grain: Optional[str] = None,
    range_: Optional[tuple] = None,
    source_dialect: str = "destination",
) -> list[WindowSpec]:
    """Enumerate the windows to extract for a table.

    Parameters
    ----------
    table:
        TableConfig (kept for backwards compatibility; ignored when *strategy*
        is provided explicitly).
    strategy:
        ``"single"``   — one window, predicate ``1=1``, ``source_filter=None``
        ``"date"``     — one window per day; requires *column*, *grain*, *range_*
        ``"pk_range"`` — N integer chunks;  requires *column*, *range_*
    column:
        Name of the column used for windowing.  Validated against
        ``_IDENTIFIER_RE`` before any SQL is composed.
    grain:
        Duration string for the date strategy.  Only ``"1d"`` is supported.
    range_:
        ``(min, max)`` tuple — ``datetime`` objects for date strategy, ``int``
        for pk_range.
    source_dialect:
        Identifier-quoting dialect for ``source_filter``.  One of
        ``"destination"`` (DuckDB, double-quotes), ``"postgres"``
        (double-quotes), ``"sqlserver"`` (square brackets), ``"mysql"``
        (backticks).  Defaults to ``"destination"`` so legacy callers that
        pass only *table* are unaffected.
    """
    if strategy == "single":
        return [
            WindowSpec(
                window_key="all",
                window_predicate="1=1",
                window_start=None,
                window_end=None,
                source_filter=None,
            )
        ]

    if column is None:
        raise ValueError("'column' is required for non-single strategies")
    if range_ is None:
        raise ValueError("'range_' is required for non-single strategies")

    # Validate column — raises ValueError on injection chars
    _quote_for_dialect(column, "destination")

    if strategy == "date":
        if grain is None:
            raise ValueError("'grain' is required for the date strategy")
        return _plan_date_windows(column, grain, range_, source_dialect)

    if strategy == "pk_range":
        return _plan_pk_range_windows(column, range_, source_dialect)

    raise ValueError(
        f"Unknown strategy {strategy!r}; supported: 'single', 'date', 'pk_range'"
    )
