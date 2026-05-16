"""Unit tests for the shared fetchmany → RecordBatch generator with heartbeat."""

from __future__ import annotations

import logging

import pyarrow as pa
import pytest

from feather_etl.sources.fetch_batches import (
    _should_emit_heartbeat,
    fetch_batches,
)


# ---------- _should_emit_heartbeat -------------------------------------------


class TestShouldEmitHeartbeat:
    def test_fires_when_row_threshold_met(self) -> None:
        assert _should_emit_heartbeat(100_000, 5.0, 100_000, 30) is True

    def test_fires_when_time_threshold_met(self) -> None:
        assert _should_emit_heartbeat(1_000, 30.0, 100_000, 30) is True

    def test_does_not_fire_below_both_thresholds(self) -> None:
        assert _should_emit_heartbeat(50_000, 15.0, 100_000, 30) is False

    def test_does_not_fire_at_zero_rows_zero_seconds(self) -> None:
        # First-call guard: t=0 / rows=0 must not produce a spurious heartbeat.
        assert _should_emit_heartbeat(0, 0.0, 100_000, 30) is False

    def test_row_trigger_off_when_every_rows_zero(self) -> None:
        # Even a huge row count won't trip a disabled row trigger.
        assert _should_emit_heartbeat(10_000_000, 1.0, 0, 30) is False

    def test_time_trigger_off_when_every_seconds_zero(self) -> None:
        assert _should_emit_heartbeat(1_000, 1_000.0, 100_000, 0) is False

    def test_globally_off_when_both_zero(self) -> None:
        assert _should_emit_heartbeat(10_000_000, 10_000.0, 0, 0) is False


# ---------- fetch_batches generator ------------------------------------------


class _FakeCursor:
    """Minimal DB-API cursor stand-in: returns pre-canned rows via fetchmany."""

    def __init__(self, rows: list[tuple], chunk_size: int | None = None) -> None:
        self._rows = list(rows)
        self._chunk = chunk_size

    def fetchmany(self, size: int) -> list[tuple]:
        n = self._chunk if self._chunk is not None else size
        chunk = self._rows[:n]
        self._rows = self._rows[n:]
        return chunk


class _FakeClock:
    """Manual clock: each call returns the next value from a scripted list."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = list(ticks)
        self._idx = 0

    def __call__(self) -> float:
        if self._idx >= len(self._ticks):
            return self._ticks[-1]
        value = self._ticks[self._idx]
        self._idx += 1
        return value


@pytest.fixture
def two_col_schema() -> pa.Schema:
    return pa.schema([pa.field("id", pa.int64()), pa.field("name", pa.string())])


class TestFetchBatches:
    def test_yields_record_batches_in_chunks(self, two_col_schema) -> None:
        rows = [(i, f"r{i}") for i in range(7)]
        cursor = _FakeCursor(rows)
        batches = list(
            fetch_batches(
                cursor=cursor,
                arrow_schema=two_col_schema,
                col_names=["id", "name"],
                batch_size=3,
                table_label="t",
                heartbeat_every_rows=0,
                heartbeat_every_seconds=0,
                clock=_FakeClock([0.0, 0.1, 0.2, 0.3]),
            )
        )
        # 7 rows / batch_size 3 → 3 yields (3+3+1)
        assert [b.num_rows for b in batches] == [3, 3, 1]
        # Schema preserved on every batch
        assert all(b.schema.equals(two_col_schema) for b in batches)

    def test_zero_rows_yields_one_empty_batch_with_schema(self, two_col_schema) -> None:
        cursor = _FakeCursor([])
        batches = list(
            fetch_batches(
                cursor=cursor,
                arrow_schema=two_col_schema,
                col_names=["id", "name"],
                batch_size=100,
                table_label="t",
                heartbeat_every_rows=0,
                heartbeat_every_seconds=0,
                clock=_FakeClock([0.0]),
            )
        )
        # Invariant: at least one batch so callers can read schema from batches[0].
        assert len(batches) == 1
        assert batches[0].num_rows == 0
        assert batches[0].schema.equals(two_col_schema)

    def test_coerce_row_callback_applied(self, two_col_schema) -> None:
        import decimal

        rows = [(decimal.Decimal("1.5"), "a"), (decimal.Decimal("2.5"), "b")]

        def coerce(val, _name):
            if isinstance(val, decimal.Decimal):
                return float(val)
            return val

        cursor = _FakeCursor(rows)
        batches = list(
            fetch_batches(
                cursor=cursor,
                arrow_schema=pa.schema(
                    [pa.field("v", pa.float64()), pa.field("n", pa.string())]
                ),
                col_names=["v", "n"],
                batch_size=10,
                table_label="t",
                coerce_row=coerce,
                heartbeat_every_rows=0,
                heartbeat_every_seconds=0,
                clock=_FakeClock([0.0, 0.1]),
            )
        )
        assert batches[0].column("v").to_pylist() == [1.5, 2.5]

    def test_heartbeat_fires_on_row_threshold(self, caplog, two_col_schema) -> None:
        rows = [(i, f"r{i}") for i in range(250)]
        cursor = _FakeCursor(rows)
        with caplog.at_level(logging.INFO, logger="feather_etl.sources.fetch_batches"):
            list(
                fetch_batches(
                    cursor=cursor,
                    arrow_schema=two_col_schema,
                    col_names=["id", "name"],
                    batch_size=100,
                    table_label="t",
                    heartbeat_every_rows=100,
                    heartbeat_every_seconds=0,  # time trigger off
                    clock=_FakeClock([0.0, 1.0, 2.0, 3.0, 4.0]),
                )
            )

        heartbeats = [
            r for r in caplog.records if r.getMessage() == "extract_heartbeat"
        ]
        # 250 rows / 100 cadence → at least 2 heartbeats (at 100, 200).
        assert len(heartbeats) >= 2
        first = heartbeats[0]
        assert first.table == "t"
        assert first.rows_loaded >= 100
        assert hasattr(first, "rows_per_sec")
        assert hasattr(first, "elapsed_s")

    def test_heartbeat_fires_on_time_threshold(self, caplog, two_col_schema) -> None:
        rows = [(i, f"r{i}") for i in range(30)]
        cursor = _FakeCursor(rows)
        # Clock jumps 60s between fetches → time trigger fires while row count
        # is well below the row threshold.
        with caplog.at_level(logging.INFO, logger="feather_etl.sources.fetch_batches"):
            list(
                fetch_batches(
                    cursor=cursor,
                    arrow_schema=two_col_schema,
                    col_names=["id", "name"],
                    batch_size=10,
                    table_label="t",
                    heartbeat_every_rows=100_000,  # not reachable
                    heartbeat_every_seconds=30,
                    clock=_FakeClock([0.0, 60.0, 120.0, 180.0]),
                )
            )

        heartbeats = [
            r for r in caplog.records if r.getMessage() == "extract_heartbeat"
        ]
        assert len(heartbeats) >= 1
