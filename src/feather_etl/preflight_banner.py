"""Pre-flight banner renderer for issue #63 adaptive extraction.

Produces a human-readable pre-flight summary for a table's ExtractPlan so the
operator can see at a glance what feather intends to do before committing to a
potentially long extraction run.

Public API
----------
render(plan, table_name, *, ...) -> str
    Returns the full banner as a single string (lines joined by ``\\n``).

_format_wall_time(seconds) -> str
    Pure helper; exposed for direct testing.
    < 60s  → "45s"
    < 1h   → "12m 30s"
    < 24h  → "6h 45m"
    >= 24h → "1d 6h"
"""

from __future__ import annotations

from feather_etl.planner import ExtractPlan

# Width of the label field before the " : " separator (matches the spec).
_LABEL_WIDTH = 15


def _format_wall_time(seconds: int) -> str:
    """Format *seconds* as a compact human-readable duration.

    Ranges
    ------
    - ``< 60``    → ``"45s"``
    - ``< 3600``  → ``"12m 30s"``
    - ``< 86400`` → ``"6h 45m"``
    - ``>= 86400``→ ``"1d 6h"``
    """
    if seconds >= 86400:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"
    if seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
    if seconds >= 60:
        minutes = seconds // 60
        secs_rem = seconds % 60
        return f"{minutes}m {secs_rem}s"
    return f"{seconds}s"


def _line(label: str, value: str) -> str:
    """Return one indented banner line: ``"    <label padded> : <value>"``."""
    return f"    {label:<{_LABEL_WIDTH}} : {value}"


def render(
    plan: ExtractPlan,
    table_name: str,
    *,
    rowcount_source: str = "estimate",
    projection_path: str | None = None,
    windows_done: int = 0,
    window_range_start: str | None = None,
    window_range_end: str | None = None,
    rows_per_sec: int | None = None,
) -> str:
    """Render the pre-flight banner for *table_name* from *plan*.

    Parameters
    ----------
    plan:
        The ``ExtractPlan`` produced by ``pick_plan()``.
    table_name:
        The logical table name shown in the banner header.
    rowcount_source:
        Where the row estimate came from (e.g. ``"sys.dm_db_partition_stats"``).
        When the default ``"estimate"`` is used, no source is appended.
    projection_path:
        Path to the projection SQL file (e.g.
        ``"transforms/bronze/zakya_dbo_sales.sql"``).  When supplied, the column
        count line shows how many columns the projection selects.
    windows_done:
        Number of windows already committed in state.  When > 0, a
        ``windows done`` line is emitted and those windows are marked for
        skipping.
    window_range_start:
        Human-readable start of the window date/key range (e.g.
        ``"2025-04-01"``).  Only shown when both start and end are provided.
    window_range_end:
        Human-readable end of the window date/key range.
    rows_per_sec:
        Recent observed throughput.  When supplied, appended to the
        ``est wall time`` line as ``"at X rows/sec"``.
    """
    lines: list[str] = []

    # Header
    lines.append(f"[{table_name}] pre-flight:")

    # rows (est)
    if rowcount_source == "estimate":
        rows_value = f"{plan.rows_estimate:,}"
    else:
        rows_value = f"{plan.rows_estimate:,} ({rowcount_source})"
    lines.append(_line("rows (est)", rows_value))

    # columns
    columns_value = str(plan.column_count)
    if projection_path is not None and plan.projection_column_count is not None:
        columns_value += f" ({plan.projection_column_count} in {projection_path})"
    lines.append(_line("columns", columns_value))

    # bucket
    lines.append(_line("bucket", plan.bucket))

    # transport
    if plan.partition_on is not None:
        transport_value = (
            f"{plan.transport}"
            f" (partition_on={plan.partition_on}, partitions={plan.partition_count})"
        )
    else:
        transport_value = plan.transport
    lines.append(_line("transport", transport_value))

    # windowing
    if plan.window_strategy == "single":
        windowing_value = "single window"
    else:
        windowing_value = (
            f"{plan.window_strategy}, {plan.window_column}, {plan.window_grain} windows"
        )
    lines.append(_line("windowing", windowing_value))

    # windows planned
    windows_planned_value = str(len(plan.windows))
    if window_range_start is not None and window_range_end is not None:
        windows_planned_value += f" ({window_range_start} → {window_range_end})"
    lines.append(_line("windows planned", windows_planned_value))

    # windows done (only when > 0)
    if windows_done > 0:
        lines.append(_line("windows done", f"{windows_done} (skipping)"))

    # windows to run
    lines.append(_line("windows to run", str(len(plan.windows) - windows_done)))

    # est wall time (only when eta_seconds is set)
    if plan.eta_seconds is not None:
        wall_time_str = _format_wall_time(plan.eta_seconds)
        if rows_per_sec is not None:
            wall_time_value = f"{wall_time_str} at {rows_per_sec:,} rows/sec"
        else:
            wall_time_value = wall_time_str
        lines.append(_line("est wall time", wall_time_value))

    return "\n".join(lines)
