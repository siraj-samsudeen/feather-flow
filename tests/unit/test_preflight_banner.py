"""Unit tests for the pre-flight banner renderer (issue #63, Task 10).

All tests are pure — no DB connections, no file I/O.
"""

from __future__ import annotations

from feather_etl.extract_windows import WindowSpec
from feather_etl.planner import ExtractPlan
from feather_etl.preflight_banner import _format_wall_time, render


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_windows(n: int) -> list[WindowSpec]:
    """Return a list of *n* dummy WindowSpec objects (content irrelevant)."""
    return [
        WindowSpec(
            window_key=f"2025-01-{i + 1:02d}",
            window_predicate=f"\"col\" >= '2025-01-{i + 1:02d}' AND \"col\" < '2025-01-{i + 2:02d}'",
            window_start=None,
            window_end=None,
            source_filter=None,
        )
        for i in range(n)
    ]


def _large_plan(
    *,
    rows_estimate: int = 49_830_000,
    column_count: int = 97,
    projection_column_count: int | None = 32,
    windows: list[WindowSpec] | None = None,
    eta_seconds: int | None = 24300,
) -> ExtractPlan:
    """Return a LARGE ExtractPlan matching the spec golden banner."""
    if windows is None:
        windows = _make_windows(408)
    return ExtractPlan(
        bucket="LARGE",
        transport="connectorx",
        partition_on="Invoice ID",
        partition_count=8,
        window_strategy="date",
        window_column="Invoice Date",
        window_grain="1d",
        windows=windows,
        blockers=[],
        eta_seconds=eta_seconds,
        rows_estimate=rows_estimate,
        column_count=column_count,
        projection_column_count=projection_column_count,
    )


def _small_plan() -> ExtractPlan:
    """Return a SMALL ExtractPlan with single-window strategy."""
    return ExtractPlan(
        bucket="SMALL",
        transport="arrow_odbc",
        partition_on=None,
        partition_count=None,
        window_strategy="single",
        window_column=None,
        window_grain=None,
        windows=_make_windows(1),
        blockers=[],
        eta_seconds=None,
        rows_estimate=5_000,
        column_count=10,
        projection_column_count=None,
    )


# ---------------------------------------------------------------------------
# Golden-string test
# ---------------------------------------------------------------------------

_GOLDEN_BANNER = """\
[zakya_dbo_sales] pre-flight:
    rows (est)      : 49,830,000 (sys.dm_db_partition_stats)
    columns         : 97 (32 in transforms/bronze/zakya_dbo_sales.sql)
    bucket          : LARGE
    transport       : connectorx (partition_on=Invoice ID, partitions=8)
    windowing       : date, Invoice Date, 1d windows
    windows planned : 408 (2025-04-01 → 2026-05-13)
    windows done    : 12 (skipping)
    windows to run  : 396
    est wall time   : 6h 45m at 2,100 rows/sec"""


def test_render_banner_matches_golden_string_for_large_bucket() -> None:
    """The rendered banner must match the spec golden string character-for-character."""
    plan = _large_plan()
    result = render(
        plan,
        "zakya_dbo_sales",
        rowcount_source="sys.dm_db_partition_stats",
        projection_path="transforms/bronze/zakya_dbo_sales.sql",
        windows_done=12,
        window_range_start="2025-04-01",
        window_range_end="2026-05-13",
        rows_per_sec=2100,
    )
    assert result == _GOLDEN_BANNER


# ---------------------------------------------------------------------------
# Small-bucket: partition_on suffix is omitted from transport line
# ---------------------------------------------------------------------------


def test_render_banner_small_bucket_omits_partition_on_line() -> None:
    """Transport line must not include a partition_on suffix for a SMALL plan."""
    plan = _small_plan()
    result = render(plan, "orders")
    lines = result.splitlines()
    transport_line = next(ln for ln in lines if "transport" in ln)
    assert "partition_on" not in transport_line
    assert "partitions" not in transport_line
    # The transport name itself is still shown
    assert "arrow_odbc" in transport_line


# ---------------------------------------------------------------------------
# No projection: column line shows only the column count
# ---------------------------------------------------------------------------


def test_render_banner_no_projection_shows_default_all_columns() -> None:
    """When projection_path is None, the column count is shown without a parenthetical."""
    plan = ExtractPlan(
        bucket="MEDIUM",
        transport="arrow_odbc",
        partition_on=None,
        partition_count=None,
        window_strategy="date",
        window_column="created_at",
        window_grain="1d",
        windows=_make_windows(30),
        blockers=[],
        eta_seconds=None,
        rows_estimate=500_000,
        column_count=20,
        projection_column_count=None,
    )
    result = render(plan, "invoices")
    columns_line = next(ln for ln in result.splitlines() if "columns" in ln)
    # Should show the count but no parenthetical
    assert ": 20" in columns_line
    assert "(" not in columns_line


# ---------------------------------------------------------------------------
# Zero committed windows: "windows done" line is omitted
# ---------------------------------------------------------------------------


def test_render_banner_zero_committed_windows_omits_skipping_line() -> None:
    """When windows_done=0 (default), the 'windows done' line must not appear."""
    plan = _large_plan()
    result = render(
        plan,
        "sales",
        rowcount_source="sys.dm_db_partition_stats",
        projection_path="transforms/bronze/sales.sql",
        windows_done=0,
    )
    assert "windows done" not in result
    assert "skipping" not in result


# ---------------------------------------------------------------------------
# Wall-time formatting: _format_wall_time pure function tests
# ---------------------------------------------------------------------------


def test_render_banner_wall_time_seconds() -> None:
    """45 seconds renders as '45s'."""
    assert _format_wall_time(45) == "45s"


def test_render_banner_wall_time_minutes() -> None:
    """750 seconds (12m 30s) renders as '12m 30s'."""
    assert _format_wall_time(750) == "12m 30s"


def test_render_banner_wall_time_hours() -> None:
    """24300 seconds (6h 45m) renders as '6h 45m'."""
    assert _format_wall_time(24300) == "6h 45m"


def test_render_banner_wall_time_days() -> None:
    """108000 seconds (30h = 1d 6h) renders as '1d 6h'."""
    assert _format_wall_time(108000) == "1d 6h"
