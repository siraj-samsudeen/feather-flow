"""Adaptive extract planner for issue #63 pre-flight sizing.

Given a TableConfig, a StreamSchema, a cheap row-count, an optional window
range, optional overrides, and defaults, ``pick_plan`` returns an immutable
``ExtractPlan`` describing:

  - Which bucket (SMALL / MEDIUM / LARGE) the table falls into
  - Which transport to use and whether to partition
  - Which window strategy and column to apply
  - The enumerated WindowSpec list
  - Any Blocker objects that must be resolved before extraction
  - A rough ETA estimate in seconds

All logic is pure — no I/O, no DB connections.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from feather_etl.config import ExtractDefaultsConfig, TableConfig
from feather_etl.extract_windows import WindowSpec, plan_windows
from feather_etl.sources import StreamSchema
from feather_etl.state import ExtractOverride

# ---------------------------------------------------------------------------
# Timestamp type substrings (case-insensitive) — any of these → date strategy
# ---------------------------------------------------------------------------

_TIMESTAMP_TYPE_SUBSTRINGS = frozenset(
    {"datetime", "datetime2", "date", "timestamp", "timestamptz"}
)


def _is_timestamp_type(type_str: str) -> bool:
    lower = type_str.lower()
    return any(sub in lower for sub in _TIMESTAMP_TYPE_SUBSTRINGS)


def _is_int_type(type_str: str) -> bool:
    return "int" in type_str.lower()


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Blocker:
    """An issue that must be resolved before extraction can safely proceed."""

    kind: Literal["wide_table", "no_window_column", "slow_transport"]
    table: str
    message: str
    bypass_flag: str


@dataclass(frozen=True)
class ExtractPlan:
    """Immutable output of ``pick_plan``."""

    bucket: Literal["SMALL", "MEDIUM", "LARGE"]
    transport: str
    partition_on: str | None
    partition_count: int | None
    window_strategy: Literal["date", "pk_range", "single"]
    window_column: str | None
    window_grain: str | None
    windows: list[WindowSpec]
    blockers: list[Blocker]
    eta_seconds: int | None
    rows_estimate: int
    column_count: int
    projection_column_count: int | None


# ---------------------------------------------------------------------------
# Auto-derive helpers
# ---------------------------------------------------------------------------


def _auto_derive_timestamp_column(discovery: StreamSchema) -> str | None:
    """Return the name of the first (lowest ordinal) timestamp column, or None."""
    for col_name, col_type in discovery.columns:
        if _is_timestamp_type(col_type):
            return col_name
    return None


def _auto_derive_int_pk_column(discovery: StreamSchema) -> str | None:
    """Return the first (lowest ordinal) column that is in primary_key and is int.

    Returns None if no PK exists, or if no PK column has an integer type.
    Discovery always populates both columns and primary_key from the same catalog
    query, so the PK column is always present in columns with a known type.
    """
    if not discovery.primary_key:
        return None

    pk_set = set(discovery.primary_key)

    # Scan columns in ordinal order for first int-typed pk column.
    for col_name, col_type in discovery.columns:
        if col_name in pk_set and _is_int_type(col_type):
            return col_name

    return None


# ---------------------------------------------------------------------------
# Bucket classification
# ---------------------------------------------------------------------------


def _classify_bucket(
    rows: int,
    col_count: int,
    defaults: ExtractDefaultsConfig,
) -> Literal["SMALL", "MEDIUM", "LARGE"]:
    """Return SMALL, MEDIUM, or LARGE based on thresholds."""
    if rows < defaults.t_small_rows and col_count < defaults.t_narrow_cols:
        return "SMALL"
    if rows >= defaults.t_large_rows and col_count >= defaults.t_narrow_cols:
        return "LARGE"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pick_plan(
    table: TableConfig,
    discovery: StreamSchema,
    cheap_rows: int,
    window_range: tuple[Any, Any] | None,
    override: ExtractOverride | None,
    defaults: ExtractDefaultsConfig,
    registered_transports: list[str],
    transport_supports_partition_on: bool,
    projection_column_count: int | None,
    *,
    source_dialect: str = "destination",
    recent_rows_per_sec: float | None = None,
    rows_already_extracted: int = 0,
) -> ExtractPlan:
    """Derive an adaptive extract plan for *table*.

    Parameters
    ----------
    table:
        TableConfig for the table being planned.
    discovery:
        StreamSchema returned by source.discover() for this table.
    cheap_rows:
        Fast approximate row count (e.g. from source.cheap_rowcount()).
    window_range:
        (min, max) range for the window column — datetime for date strategy,
        int for pk_range, None if unknown/not applicable.
    override:
        Stored ExtractOverride from state, or None.  Override is field-by-field:
        only non-None fields shadow auto-derive.
    defaults:
        ExtractDefaultsConfig with threshold values.
    registered_transports:
        Names of transports available in this installation (e.g. ["arrow_odbc",
        "connectorx"]).
    transport_supports_partition_on:
        Whether the current transport can do parallel partitioned reads.
    projection_column_count:
        Number of columns in the configured projection, or None if no projection
        is configured.
    source_dialect:
        SQL identifier quoting dialect for source-side filters.
    recent_rows_per_sec:
        Observed throughput from recent runs; used for ETA if supplied.
    rows_already_extracted:
        Rows already committed for this table; subtracted from cheap_rows for ETA.
    """
    column_count = len(discovery.columns)

    # -----------------------------------------------------------------------
    # Bucket
    # -----------------------------------------------------------------------
    bucket = _classify_bucket(cheap_rows, column_count, defaults)

    # -----------------------------------------------------------------------
    # Auto-derive: window column and int PK column
    # -----------------------------------------------------------------------
    auto_timestamp_col = _auto_derive_timestamp_column(discovery)
    auto_int_pk_col = _auto_derive_int_pk_column(discovery)

    # Field-by-field override precedence
    if override is not None and override.window_column is not None:
        effective_window_col = override.window_column
    else:
        effective_window_col = auto_timestamp_col

    if override is not None and override.partition_on is not None:
        effective_partition_on_col = override.partition_on
    else:
        effective_partition_on_col = auto_int_pk_col

    # -----------------------------------------------------------------------
    # Window strategy
    # -----------------------------------------------------------------------
    if override is not None and override.window_strategy is not None:
        window_strategy: Literal["date", "pk_range", "single"] = (
            override.window_strategy  # type: ignore[assignment]
        )
    else:
        # Auto-derive strategy from available columns
        if effective_window_col is not None and auto_timestamp_col is not None:
            window_strategy = "date"
        elif auto_int_pk_col is not None and effective_window_col is None:
            window_strategy = "pk_range"
        elif auto_timestamp_col is None and auto_int_pk_col is None:
            window_strategy = "single"
        else:
            # Has a timestamp column (possibly via override) → date strategy
            window_strategy = "date"

    # For SMALL bucket, always single
    if bucket == "SMALL":
        window_strategy = "single"

    # -----------------------------------------------------------------------
    # Window grain
    # -----------------------------------------------------------------------
    if override is not None and override.window_grain is not None:
        window_grain: str | None = override.window_grain
    else:
        window_grain = "1d" if window_strategy == "date" else None

    # -----------------------------------------------------------------------
    # Transport and partition_on for LARGE
    # -----------------------------------------------------------------------
    transport: str
    partition_on: str | None
    partition_count: int | None

    if (
        bucket == "LARGE"
        and "connectorx" in registered_transports
        and transport_supports_partition_on
    ):
        transport = "connectorx"
        partition_on = effective_partition_on_col
        partition_count = min(8, max(2, cheap_rows // 1_000_000))
    else:
        # Use first registered transport as default
        transport = registered_transports[0] if registered_transports else "arrow_odbc"
        partition_on = None
        partition_count = None

    # -----------------------------------------------------------------------
    # Blockers
    # -----------------------------------------------------------------------
    blockers: list[Blocker] = []

    # wide_table blocker
    if column_count > defaults.t_wide_cols and projection_column_count is None:
        blockers.append(
            Blocker(
                kind="wide_table",
                table=table.name,
                message=(
                    f"Table '{table.name}' has {column_count} columns "
                    f"(threshold: {defaults.t_wide_cols}). "
                    f"Configure a projection in schema_{table.source_name or table.name}.json "
                    f"to narrow the extract."
                ),
                bypass_flag="--accept-wide",
            )
        )

    # no_window_column blocker (MEDIUM and LARGE only)
    if bucket in ("MEDIUM", "LARGE"):
        # No derivable window column AND no override window column
        has_window = effective_window_col is not None or auto_int_pk_col is not None
        # If bucket is MEDIUM and strategy ended up single due to no window → blocker
        if not has_window and (
            override is None
            or (override.window_column is None and override.window_strategy is None)
        ):
            blockers.append(
                Blocker(
                    kind="no_window_column",
                    table=table.name,
                    message=(
                        f"Table '{table.name}' has no derivable window column "
                        f"(no timestamp or integer primary key found). "
                        f"Use --single-window to extract without windowing, or set "
                        f"an override with `feather override set {table.name} --window-column <col>`."
                    ),
                    bypass_flag="--single-window",
                )
            )

    # slow_transport blocker (LARGE only, when connectorx not available)
    if bucket == "LARGE" and "connectorx" not in registered_transports:
        blockers.append(
            Blocker(
                kind="slow_transport",
                table=table.name,
                message=(
                    f"Table '{table.name}' is LARGE ({cheap_rows:,} rows) but "
                    f"connectorx is not installed. "
                    f"Install connectorx for parallel reads, or use "
                    f"--accept-slow-transport to proceed with the current transport."
                ),
                bypass_flag="--accept-slow-transport",
            )
        )

    # -----------------------------------------------------------------------
    # Windows
    # -----------------------------------------------------------------------
    if window_strategy == "single":
        windows = plan_windows(table)
    elif window_strategy == "date":
        if window_range is not None and effective_window_col is not None:
            windows = plan_windows(
                table,
                strategy="date",
                column=effective_window_col,
                grain=window_grain or "1d",
                range_=window_range,
                source_dialect=source_dialect,
            )
        else:
            # Fall back to single if range or column is missing
            windows = plan_windows(table)
    elif window_strategy == "pk_range":
        if window_range is not None and effective_partition_on_col is not None:
            windows = plan_windows(
                table,
                strategy="pk_range",
                column=effective_partition_on_col,
                range_=window_range,
                source_dialect=source_dialect,
            )
        else:
            windows = plan_windows(table)
    else:
        windows = plan_windows(table)

    # -----------------------------------------------------------------------
    # ETA
    # -----------------------------------------------------------------------
    remaining_rows = max(0, cheap_rows - rows_already_extracted)
    eta_seconds: int | None

    if remaining_rows == 0:
        eta_seconds = None
    elif recent_rows_per_sec is not None:
        eta_seconds = int(remaining_rows / recent_rows_per_sec)
    elif transport == "connectorx" and partition_on is not None:
        eta_seconds = int(remaining_rows / 6000.0)
    else:
        eta_seconds = int(remaining_rows / 2000.0)

    # -----------------------------------------------------------------------
    # Return plan
    # -----------------------------------------------------------------------
    # Resolve effective window column to store in plan
    plan_window_column: str | None
    if window_strategy == "single":
        plan_window_column = None
    elif window_strategy == "date":
        plan_window_column = effective_window_col
    elif window_strategy == "pk_range":
        plan_window_column = effective_partition_on_col
    else:
        plan_window_column = effective_window_col

    return ExtractPlan(
        bucket=bucket,
        transport=transport,
        partition_on=partition_on,
        partition_count=partition_count,
        window_strategy=window_strategy,
        window_column=plan_window_column,
        window_grain=window_grain,
        windows=windows,
        blockers=blockers,
        eta_seconds=eta_seconds,
        rows_estimate=cheap_rows,
        column_count=column_count,
        projection_column_count=projection_column_count,
    )
