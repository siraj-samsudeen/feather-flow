"""Unit tests for the pick_plan() pure function (issue #63, Task 9).

All tests are pure — no DB connections, no file I/O.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from feather_etl.config import ExtractDefaultsConfig, TableConfig
from feather_etl.planner import Blocker, ExtractPlan, pick_plan  # noqa: F401
from feather_etl.sources import StreamSchema
from feather_etl.state import ExtractOverride


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _defaults(
    t_small_rows: int = 100_000,
    t_large_rows: int = 5_000_000,
    t_narrow_cols: int = 25,
    t_wide_cols: int = 60,
) -> ExtractDefaultsConfig:
    return ExtractDefaultsConfig(
        t_small_rows=t_small_rows,
        t_large_rows=t_large_rows,
        t_narrow_cols=t_narrow_cols,
        t_wide_cols=t_wide_cols,
    )


def _table(name: str = "orders", transport: str = "arrow_odbc") -> TableConfig:
    return TableConfig(
        name=name,
        source_table=name,
        strategy="full",
        database="src",
        source_name="src",
    )


def _schema_with_timestamp(
    num_cols: int = 10,
    pk: list[str] | None = None,
    timestamp_col_ordinal: int = 2,
    timestamp_col_name: str = "created_at",
    int_pk_col_name: str | None = None,
) -> StreamSchema:
    """Return a StreamSchema that has a timestamp column at *timestamp_col_ordinal* (0-based).

    When *int_pk_col_name* is supplied, the named column is prepended to the
    columns list with type ``bigint`` so it actually appears in ``columns`` and
    ``primary_key`` consistently.
    """
    columns: list[tuple[str, str]] = []
    if int_pk_col_name is not None:
        columns.append((int_pk_col_name, "bigint"))
    for i in range(num_cols):
        if i == timestamp_col_ordinal:
            columns.append((timestamp_col_name, "datetime2"))
        else:
            columns.append((f"col_{i}", "varchar"))
    return StreamSchema(
        name="dbo.orders",
        columns=columns,
        primary_key=pk or ["id"],
        supports_incremental=True,
    )


def _schema_no_timestamp(
    num_cols: int = 10,
    int_pk: bool = True,
    pk_col: str = "id",
) -> StreamSchema:
    """Return a StreamSchema with no timestamp column, optionally with an int PK."""
    columns: list[tuple[str, str]] = [
        (pk_col, "bigint") if int_pk and i == 0 else (f"col_{i}", "varchar")
        for i in range(num_cols)
    ]
    return StreamSchema(
        name="dbo.orders",
        columns=columns,
        primary_key=[pk_col] if int_pk else None,
        supports_incremental=int_pk,
    )


def _schema_no_window(num_cols: int = 10) -> StreamSchema:
    """Return a StreamSchema with no timestamp and no int PK — cannot auto-derive window."""
    columns = [(f"col_{i}", "varchar") for i in range(num_cols)]
    return StreamSchema(
        name="dbo.orders",
        columns=columns,
        primary_key=None,
        supports_incremental=False,
    )


def _override(**kwargs) -> ExtractOverride:
    return ExtractOverride(
        table_name="orders",
        window_strategy=kwargs.get("window_strategy"),
        window_column=kwargs.get("window_column"),
        window_grain=kwargs.get("window_grain"),
        partition_on=kwargs.get("partition_on"),
        created_at=datetime(2026, 1, 1),
    )


_DATE_RANGE = (datetime(2026, 1, 1), datetime(2026, 1, 3))


# ---------------------------------------------------------------------------
# Bucket picking
# ---------------------------------------------------------------------------


def test_pick_plan_small_bucket_picks_single_window() -> None:
    """SMALL tables get a single window and the table's own transport."""
    schema = _schema_with_timestamp(num_cols=10)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=50_000,  # < t_small_rows=100K
        window_range=None,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.bucket == "SMALL"
    assert plan.window_strategy == "single"
    assert len(plan.windows) == 1
    assert plan.windows[0].window_key == "all"
    assert plan.blockers == []


def test_pick_plan_medium_bucket_picks_date_windowing() -> None:
    """MEDIUM tables auto-derive date windowing when a timestamp column exists."""
    schema = _schema_with_timestamp(num_cols=10)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,  # >= t_small_rows but < t_large_rows
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.bucket == "MEDIUM"
    assert plan.window_strategy == "date"
    assert plan.window_column == "created_at"
    assert plan.blockers == []
    # 3 Jan - 1 Jan = 3 windows (Jan 1, Jan 2, Jan 3)
    assert len(plan.windows) == 3


def test_pick_plan_large_bucket_picks_connectorx_when_supported() -> None:
    """LARGE tables propose connectorx + partition_on when connectorx is registered."""
    schema = _schema_with_timestamp(num_cols=30, pk=["id"], int_pk_col_name="id")
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=10_000_000,  # >= t_large_rows AND cols >= t_narrow_cols
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc", "connectorx"],
        transport_supports_partition_on=True,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.bucket == "LARGE"
    assert plan.transport == "connectorx"
    assert plan.partition_on == "id"
    # No slow_transport blocker since connectorx is available
    kinds = [b.kind for b in plan.blockers]
    assert "slow_transport" not in kinds


def test_pick_plan_large_bucket_blocks_when_connectorx_absent() -> None:
    """LARGE tables emit slow_transport blocker when connectorx is not available."""
    schema = _schema_with_timestamp(num_cols=30)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=10_000_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],  # no connectorx
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.bucket == "LARGE"
    kinds = [b.kind for b in plan.blockers]
    assert "slow_transport" in kinds


# ---------------------------------------------------------------------------
# Wide-table blockers
# ---------------------------------------------------------------------------


def test_pick_plan_wide_table_no_projection_blocks() -> None:
    """A table wider than t_wide_cols with no projection blocks with wide_table."""
    schema = _schema_with_timestamp(num_cols=80)  # > t_wide_cols=60
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,  # no projection configured
        source_dialect="sqlserver",
    )
    kinds = [b.kind for b in plan.blockers]
    assert "wide_table" in kinds


def test_pick_plan_wide_table_with_projection_no_block() -> None:
    """A wide table with a projection configured does NOT block."""
    schema = _schema_with_timestamp(num_cols=80)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=20,  # projection reduces columns
        source_dialect="sqlserver",
    )
    kinds = [b.kind for b in plan.blockers]
    assert "wide_table" not in kinds


# ---------------------------------------------------------------------------
# no_window_column blocker
# ---------------------------------------------------------------------------


def test_pick_plan_no_window_column_medium_blocks_no_window_column() -> None:
    """MEDIUM table with no derivable window column blocks with no_window_column."""
    schema = _schema_no_window(num_cols=10)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=None,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.bucket == "MEDIUM"
    kinds = [b.kind for b in plan.blockers]
    assert "no_window_column" in kinds


def test_pick_plan_no_window_column_small_picks_single_no_block() -> None:
    """SMALL table with no derivable window column still picks single — no blocker."""
    schema = _schema_no_window(num_cols=10)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=50_000,  # SMALL
        window_range=None,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.bucket == "SMALL"
    assert plan.window_strategy == "single"
    kinds = [b.kind for b in plan.blockers]
    assert "no_window_column" not in kinds


# ---------------------------------------------------------------------------
# Override precedence
# ---------------------------------------------------------------------------


def test_pick_plan_override_window_column_beats_auto_derive() -> None:
    """An override window_column takes precedence over the auto-derived one."""
    # Schema has "created_at" at ordinal 2 — auto-derive would pick it.
    schema = _schema_with_timestamp(
        num_cols=10,
        timestamp_col_ordinal=2,
        timestamp_col_name="created_at",
    )
    # But override says to use "modified_at".
    ov = _override(window_column="modified_at")
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=_DATE_RANGE,
        override=ov,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.window_column == "modified_at"


def test_pick_plan_override_partial_inherits_other_fields_from_auto_derive() -> None:
    """Setting only override.window_column leaves window_strategy auto-derived (date)."""
    schema = _schema_with_timestamp(num_cols=10)
    # Override only specifies window_column — strategy should still be auto-derived as date.
    ov = _override(window_column="modified_at")
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=_DATE_RANGE,
        override=ov,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    # strategy was not in override → auto-derived from schema (has timestamp → "date")
    assert plan.window_strategy == "date"
    # column came from override
    assert plan.window_column == "modified_at"


# ---------------------------------------------------------------------------
# Auto-derive column selection (ordinal order)
# ---------------------------------------------------------------------------


def test_pick_plan_auto_derive_date_picks_first_timestamp_column_by_ordinal() -> None:
    """Auto-derive picks the timestamp column earliest in the column list."""
    # Two timestamp columns; "created_at" at ordinal 1, "updated_at" at ordinal 5.
    columns = [
        ("id", "bigint"),
        ("created_at", "datetime2"),
        ("name", "varchar"),
        ("amount", "decimal"),
        ("status", "varchar"),
        ("updated_at", "datetime"),
    ]
    schema = StreamSchema(
        name="dbo.orders",
        columns=columns,
        primary_key=["id"],
        supports_incremental=True,
    )
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    # "created_at" at ordinal 1 should be picked, not "updated_at" at ordinal 5.
    assert plan.window_column == "created_at"


def test_pick_plan_auto_derive_pk_range_picks_first_pk_column_by_ordinal() -> None:
    """Auto-derive pk_range picks the first integer PK by ordinal when no timestamp."""
    # Composite PK: col_b (int, ordinal 2) and col_a (int, ordinal 0).
    # PK list order: ["col_b", "col_a"]. Ordinal search: col_b at pos 2, col_a at pos 0.
    # First by ordinal position in columns list should be col_a.
    columns = [
        ("col_a", "int"),
        ("name", "varchar"),
        ("col_b", "bigint"),
        ("status", "varchar"),
    ]
    schema = StreamSchema(
        name="dbo.widget",
        columns=columns,
        primary_key=["col_b", "col_a"],  # PK list order differs from column ordinal
        supports_incremental=True,
    )
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=(0, 1_000_000),
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    # col_a is at ordinal 0, col_b at ordinal 2 → pick col_a
    assert plan.window_column == "col_a"
    assert plan.window_strategy == "pk_range"
    # pk_range with a real range must produce multiple windows — not the single-window
    # degenerate case that a wrong-variable fallback would produce.
    assert len(plan.windows) > 1, (
        f"pk_range strategy should produce multiple windows, got {len(plan.windows)}"
    )
    assert plan.windows[0].window_key != "all", (
        "pk_range strategy must not fall back to the single-window 'all' key"
    )


# ---------------------------------------------------------------------------
# Partition count formula
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rows,expected_partition_count",
    [
        (100_000, 2),
        (1_000_000, 2),
        (5_000_000, 5),
        (10_000_000, 8),
        (50_000_000, 8),
    ],
)
def test_pick_plan_partition_count_formula(
    rows: int, expected_partition_count: int
) -> None:
    """partition_count = min(8, max(2, rows // 1_000_000))."""
    # Use a LARGE bucket scenario (>= t_large_rows, >= t_narrow_cols) with connectorx.
    schema = _schema_with_timestamp(num_cols=30, pk=["id"], int_pk_col_name="id")
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=rows,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(t_large_rows=1),  # force LARGE bucket for any row count
        registered_transports=["arrow_odbc", "connectorx"],
        transport_supports_partition_on=True,
        projection_column_count=None,
        source_dialect="sqlserver",
    )
    assert plan.partition_count == expected_partition_count


# ---------------------------------------------------------------------------
# ETA estimation
# ---------------------------------------------------------------------------


def test_pick_plan_eta_uses_runs_history_when_available() -> None:
    """ETA uses recent_rows_per_sec when supplied."""
    schema = _schema_with_timestamp(num_cols=10)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
        recent_rows_per_sec=5000.0,
    )
    # 500_000 / 5000 = 100
    assert plan.eta_seconds == 100


def test_pick_plan_eta_falls_back_to_arrow_odbc_baseline() -> None:
    """ETA uses 2000 rows/sec baseline when no history and transport != connectorx."""
    schema = _schema_with_timestamp(num_cols=10)
    plan = pick_plan(
        table=_table(transport="arrow_odbc"),
        discovery=schema,
        cheap_rows=200_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
        recent_rows_per_sec=None,  # no history
    )
    # baseline arrow_odbc = 2000 rows/sec; 200_000 / 2000 = 100
    assert plan.eta_seconds == 100


def test_pick_plan_eta_falls_back_to_connectorx_baseline() -> None:
    """ETA uses 6000 rows/sec baseline for connectorx transport with partition_on."""
    schema = _schema_with_timestamp(num_cols=30, pk=["id"], int_pk_col_name="id")
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=6_000_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc", "connectorx"],
        transport_supports_partition_on=True,
        projection_column_count=None,
        source_dialect="sqlserver",
        recent_rows_per_sec=None,  # no history — fall back to baseline
    )
    # connectorx + partition_on baseline = 6000 rows/sec; 6_000_000 / 6000 = 1000
    assert plan.eta_seconds == 1000


def test_pick_plan_eta_omitted_when_zero_windows_to_run() -> None:
    """eta_seconds is None when rows_already_extracted >= rows_estimate."""
    schema = _schema_with_timestamp(num_cols=10)
    plan = pick_plan(
        table=_table(),
        discovery=schema,
        cheap_rows=500_000,
        window_range=_DATE_RANGE,
        override=None,
        defaults=_defaults(),
        registered_transports=["arrow_odbc"],
        transport_supports_partition_on=False,
        projection_column_count=None,
        source_dialect="sqlserver",
        rows_already_extracted=500_000,  # already done
    )
    assert plan.eta_seconds is None
