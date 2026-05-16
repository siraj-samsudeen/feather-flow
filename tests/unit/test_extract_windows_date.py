"""Unit tests for plan_windows date strategy (#63).

Tests:
- Single-window degenerate case (preserves today's behaviour)
- Date enumeration: one window per day
- Half-open intervals [start, end)
- Partial last day still emits a window that covers max_dt
- Injection rejection via column validation
- source_filter quoting per dialect (destination, sqlserver, postgres, mysql)
"""

from __future__ import annotations

import pytest
from datetime import datetime

from feather_etl.extract_windows import plan_windows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(iso: str) -> datetime:
    """Parse a compact ISO datetime string."""
    return datetime.fromisoformat(iso)


# ---------------------------------------------------------------------------
# Single-window (backwards-compat)
# ---------------------------------------------------------------------------


def test_plan_windows_single_strategy_returns_one_window() -> None:
    windows = plan_windows(strategy="single")
    assert len(windows) == 1
    w = windows[0]
    assert w.window_key == "all"
    assert w.window_predicate == "1=1"
    assert w.window_start is None
    assert w.window_end is None
    assert w.source_filter is None


# ---------------------------------------------------------------------------
# Date strategy — one window per day
# ---------------------------------------------------------------------------


def test_plan_windows_date_strategy_emits_one_per_day() -> None:
    windows = plan_windows(
        strategy="date",
        column="invoice_date",
        grain="1d",
        range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-03T23:59:59")),
        source_dialect="destination",
    )
    # Three days: Apr 1, Apr 2, Apr 3
    assert len(windows) == 3
    assert [w.window_key for w in windows] == ["2026-04-01", "2026-04-02", "2026-04-03"]


def test_plan_windows_date_strategy_inclusive_start_exclusive_end() -> None:
    windows = plan_windows(
        strategy="date",
        column="invoice_date",
        grain="1d",
        range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-01T23:59:59")),
        source_dialect="destination",
    )
    assert len(windows) == 1
    w = windows[0]
    # Half-open [start, end): start = Apr 1 00:00, end = Apr 2 00:00
    assert "\"invoice_date\" >= '2026-04-01T00:00:00'" in w.window_predicate
    assert "\"invoice_date\" < '2026-04-02T00:00:00'" in w.window_predicate
    assert w.window_start == _dt("2026-04-01T00:00:00")
    assert w.window_end == _dt("2026-04-02T00:00:00")


def test_plan_windows_date_strategy_partial_last_day_emits_until_max() -> None:
    """A range that doesn't align to day boundaries still covers the last partial day."""
    windows = plan_windows(
        strategy="date",
        column="invoice_date",
        grain="1d",
        range_=(_dt("2026-04-01T06:00:00"), _dt("2026-04-02T14:30:00")),
        source_dialect="destination",
    )
    # Apr 1 (starting at 06:00) and Apr 2 (partial day)
    assert len(windows) == 2
    keys = [w.window_key for w in windows]
    assert "2026-04-01" in keys
    assert "2026-04-02" in keys
    # Last window must end AFTER max_dt
    last = windows[-1]
    assert last.window_end > _dt("2026-04-02T14:30:00")


# ---------------------------------------------------------------------------
# Injection rejection
# ---------------------------------------------------------------------------


def test_plan_windows_rejects_window_column_with_sql_injection_chars() -> None:
    with pytest.raises(ValueError, match="column"):
        plan_windows(
            strategy="date",
            column="x; DROP TABLE foo--",
            grain="1d",
            range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-01T23:59:59")),
            source_dialect="destination",
        )


# ---------------------------------------------------------------------------
# source_filter quoting by dialect
# ---------------------------------------------------------------------------


def test_window_spec_source_filter_renders_in_destination_quoting() -> None:
    windows = plan_windows(
        strategy="date",
        column="invoice_date",
        grain="1d",
        range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-01T23:59:59")),
        source_dialect="destination",
    )
    w = windows[0]
    assert w.source_filter is not None
    # destination (DuckDB) uses double-quotes for identifiers
    assert '"invoice_date"' in w.source_filter


def test_window_spec_source_filter_quotes_for_sqlserver_dialect() -> None:
    windows = plan_windows(
        strategy="date",
        column="invoice_date",
        grain="1d",
        range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-01T23:59:59")),
        source_dialect="sqlserver",
    )
    w = windows[0]
    assert w.source_filter is not None
    # SQL Server uses square brackets
    assert "[invoice_date]" in w.source_filter


def test_window_spec_source_filter_quotes_for_postgres_dialect() -> None:
    windows = plan_windows(
        strategy="date",
        column="invoice_date",
        grain="1d",
        range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-01T23:59:59")),
        source_dialect="postgres",
    )
    w = windows[0]
    assert w.source_filter is not None
    # Postgres uses double-quotes
    assert '"invoice_date"' in w.source_filter


def test_window_spec_source_filter_quotes_for_mysql_dialect() -> None:
    windows = plan_windows(
        strategy="date",
        column="invoice_date",
        grain="1d",
        range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-01T23:59:59")),
        source_dialect="mysql",
    )
    w = windows[0]
    assert w.source_filter is not None
    # MySQL uses backticks
    assert "`invoice_date`" in w.source_filter


def test_plan_windows_rejects_unsupported_grain() -> None:
    with pytest.raises(ValueError, match="grain"):
        plan_windows(
            strategy="date",
            column="invoice_date",
            grain="1h",
            range_=(_dt("2026-04-01T00:00:00"), _dt("2026-04-01T23:59:59")),
            source_dialect="destination",
        )
