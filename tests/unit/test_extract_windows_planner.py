"""Unit tests for the (currently single-window) extract planner.

Issue #63 will replace `plan_windows` with a multi-strategy planner; the
WindowSpec / WindowResult contract this module defines is what the
destination consumes and is locked here so #63 can extend without
churning destinations or state."""

from __future__ import annotations

import pyarrow as pa

from feather_etl.config import TableConfig
from feather_etl.extract import _iter_source_batches
from feather_etl.extract_windows import WindowSpec, plan_windows


def _make_table(name: str = "sales") -> TableConfig:
    # Minimal — strategy is required; using "full" as the simplest valid value.
    return TableConfig(
        name=name,
        source_table=name,
        strategy="full",
        database="src",
        source_name="src",
    )


def test_plan_windows_returns_one_window_with_full_table_predicate() -> None:
    plans = plan_windows(_make_table("sales"))
    assert plans == [
        WindowSpec(
            window_key="all",
            window_predicate="1=1",
            window_start=None,
            window_end=None,
        )
    ]


def test_window_spec_is_frozen_dataclass() -> None:
    spec = WindowSpec(
        window_key="2026-05-12",
        window_predicate="[Invoice Date] = '2026-05-12'",
        window_start=None,
        window_end=None,
    )
    # Frozen → hashable → can live in a set (used to skip committed windows).
    assert spec in {spec}


# ---------------------------------------------------------------------------
# _iter_source_batches passes window.source_filter to source.extract_batches
# ---------------------------------------------------------------------------


class _StubBatchingSource:
    """Minimal stub for a SQL source that records the filter kwarg it received."""

    def __init__(self) -> None:
        self.recorded_filter: str | None = _SENTINEL  # type: ignore[assignment]

    def extract_batches(
        self,
        table: str,
        *,
        filter: str | None = None,  # noqa: A002
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ):
        self.recorded_filter = filter
        # Yield one empty batch so the iterator is exhausted cleanly.
        schema = pa.schema([("id", pa.int64())])
        yield pa.RecordBatch.from_pylist([], schema=schema)


_SENTINEL = object()


def test_extract_passes_window_source_filter_to_source() -> None:
    """_iter_source_batches must forward window.source_filter as `filter=` kwarg.

    Without this fix each window re-fetches the full source table, making
    multi-window extracts produce incorrect bronze data (only the last window
    survives after repeated DELETE+INSERT cycles).
    """
    source_filter = "\"created_at\" >= '2026-04-01' AND \"created_at\" < '2026-04-02'"
    stub = _StubBatchingSource()

    # Exhaust the iterator — that's when extract_batches is actually called.
    list(
        _iter_source_batches(
            source=stub,
            source_table="orders",
            heartbeat_every_rows=100_000,
            heartbeat_every_seconds=30,
            source_filter=source_filter,
        )
    )

    assert stub.recorded_filter == source_filter, (
        f"Expected filter={source_filter!r} to be forwarded to extract_batches, "
        f"got {stub.recorded_filter!r}"
    )


def test_extract_passes_none_source_filter_for_single_window() -> None:
    """Single-window case: source_filter=None must be forwarded unchanged (full-table fetch)."""
    stub = _StubBatchingSource()

    list(
        _iter_source_batches(
            source=stub,
            source_table="orders",
            heartbeat_every_rows=100_000,
            heartbeat_every_seconds=30,
            source_filter=None,
        )
    )

    assert stub.recorded_filter is None, (
        f"Single-window path must pass filter=None, got {stub.recorded_filter!r}"
    )
