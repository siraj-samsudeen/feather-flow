"""Window enumeration for batched-append extracts (#62).

Currently emits a single window covering the whole table — the degenerate
case. Issue #63 will add `date` and `pk_range` strategies driven by curation;
the WindowSpec contract here is locked so #63 needs no destination changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from feather_etl.config import TableConfig


@dataclass(frozen=True)
class WindowSpec:
    """One unit of resumable work for a table extract.

    `window_predicate` is interpolated directly into a DELETE statement on
    the bronze table — it MUST be safe SQL composed by the planner, never
    raw user input. The single-window case uses `1=1` (delete everything).

    Today the planner does not push any per-window filter into the source
    fetch — every window re-fetches the whole source table and relies on
    DELETE-WHERE-predicate for window isolation. Issue #63 will likely
    extend either this contract (adding a source-filter field) or the
    planner's calling convention to push the predicate into the source's
    extract call.
    """

    window_key: str
    window_predicate: str
    window_start: datetime | None
    window_end: datetime | None


@dataclass
class WindowResult:
    """Per-window outcome returned by destination.load_batched_append.

    Only constructed for committed windows. Failures raise the underlying
    exception from within load_batched_append after a transaction rollback.
    """

    window_key: str
    rows_loaded: int


def plan_windows(table: TableConfig) -> list[WindowSpec]:
    """Enumerate the windows to extract for a table.

    Today: always returns one window covering the whole table. Issue #63
    extends this to read window_strategy/window_column from curation.
    """
    return [
        WindowSpec(
            window_key="all",
            window_predicate="1=1",
            window_start=None,
            window_end=None,
        )
    ]
