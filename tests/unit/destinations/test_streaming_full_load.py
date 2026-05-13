"""Unit tests for DuckDBDestination.streaming_full_load — the per-batch
streaming load that preserves swap atomicity for `feather extract`."""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest

from feather_etl.destinations.duckdb import DuckDBDestination


@pytest.fixture
def dest(tmp_path):
    d = DuckDBDestination(path=tmp_path / "feather_data.duckdb")
    d.setup_schemas()
    return d


def _read_table(dest, table: str) -> pa.Table:
    con = dest._connect()
    try:
        return con.execute(f"SELECT * FROM {table}").to_arrow_table()
    finally:
        con.close()


def _table_exists(dest, schema: str, table: str) -> bool:
    con = dest._connect()
    try:
        result = con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=? AND table_name=?",
            [schema, table],
        ).fetchone()
        return result is not None
    finally:
        con.close()


class TestStreamingFullLoadHappyPath:
    def test_writes_rows_and_creates_final_table(self, dest) -> None:
        schema = pa.schema([pa.field("id", pa.int64()), pa.field("v", pa.string())])
        b1 = pa.RecordBatch.from_pydict({"id": [1, 2], "v": ["a", "b"]}, schema=schema)
        b2 = pa.RecordBatch.from_pydict({"id": [3], "v": ["c"]}, schema=schema)

        with dest.streaming_full_load("bronze.foo", "run-1") as session:
            session.append(b1)
            session.append(b2)
        assert session.rows_loaded == 3

        out = _read_table(dest, "bronze.foo")
        assert out.num_rows == 3
        assert out.column("id").to_pylist() == [1, 2, 3]
        # ETL metadata columns added by the streaming load
        assert "_etl_loaded_at" in out.column_names
        assert "_etl_run_id" in out.column_names
        assert out.column("_etl_run_id").to_pylist() == ["run-1"] * 3

    def test_replaces_existing_table_atomically(self, dest) -> None:
        schema = pa.schema([pa.field("id", pa.int64())])
        b1 = pa.RecordBatch.from_pydict({"id": [1, 2, 3]}, schema=schema)
        with dest.streaming_full_load("bronze.foo", "run-1") as s:
            s.append(b1)

        b2 = pa.RecordBatch.from_pydict({"id": [10, 20]}, schema=schema)
        with dest.streaming_full_load("bronze.foo", "run-2") as s:
            s.append(b2)

        out = _read_table(dest, "bronze.foo")
        # Replacement, not append — old rows are gone.
        assert sorted(out.column("id").to_pylist()) == [10, 20]
        # Staging table was renamed away, not left behind.
        assert not _table_exists(dest, "bronze", "foo_new")


class TestStreamingFullLoadFailurePath:
    def test_exception_drops_staging_and_preserves_existing_final(self, dest) -> None:
        # Seed the destination with a known-good table.
        schema = pa.schema([pa.field("id", pa.int64())])
        with dest.streaming_full_load("bronze.foo", "run-1") as s:
            s.append(pa.RecordBatch.from_pydict({"id": [1, 2, 3]}, schema=schema))

        # Crash mid-extract on the second run.
        with pytest.raises(RuntimeError):
            with dest.streaming_full_load("bronze.foo", "run-2") as s:
                s.append(pa.RecordBatch.from_pydict({"id": [99]}, schema=schema))
                raise RuntimeError("simulated extract failure")

        # Previous-good data is intact — the atomic swap never ran.
        out = _read_table(dest, "bronze.foo")
        assert sorted(out.column("id").to_pylist()) == [1, 2, 3]
        # Staging table was cleaned up.
        assert not _table_exists(dest, "bronze", "foo_new")

    def test_zero_batches_no_staging_no_final_change(self, dest) -> None:
        # No append() call → staging never created → final left alone.
        with dest.streaming_full_load("bronze.foo", "run-1") as s:
            pass
        assert not _table_exists(dest, "bronze", "foo")
        assert not _table_exists(dest, "bronze", "foo_new")

    def test_empty_first_batch_still_creates_typed_final(self, dest) -> None:
        # The streaming load contract: an empty batch with a schema still
        # results in a final table being created (zero rows, right columns).
        schema = pa.schema([pa.field("id", pa.int64()), pa.field("v", pa.string())])
        empty = pa.RecordBatch.from_pylist([], schema=schema)
        with dest.streaming_full_load("bronze.foo", "run-1") as s:
            s.append(empty)
        assert _table_exists(dest, "bronze", "foo")
        out = _read_table(dest, "bronze.foo")
        assert out.num_rows == 0
        # ETL metadata columns still added
        assert "_etl_loaded_at" in out.column_names
        assert "_etl_run_id" in out.column_names

    def test_recovers_from_stale_staging_on_previous_crash(self, dest) -> None:
        # Simulate a crash that leaked a stale staging table from a prior run.
        schema = pa.schema([pa.field("id", pa.int64())])
        con = dest._connect()
        try:
            con.execute(
                "CREATE TABLE bronze.foo_new (id BIGINT, _etl_loaded_at TIMESTAMP, _etl_run_id VARCHAR)"
            )
            con.execute(
                "INSERT INTO bronze.foo_new VALUES (999, CURRENT_TIMESTAMP, 'stale')"
            )
        finally:
            con.close()
        assert _table_exists(dest, "bronze", "foo_new")

        # New session should drop the stale staging and start clean.
        with dest.streaming_full_load("bronze.foo", "run-fresh") as s:
            s.append(pa.RecordBatch.from_pydict({"id": [1, 2]}, schema=schema))
        out = _read_table(dest, "bronze.foo")
        assert sorted(out.column("id").to_pylist()) == [1, 2]
        # Stale row never made it into the final table.
        assert 999 not in out.column("id").to_pylist()


class TestStreamingFullLoadAPI:
    def test_append_outside_session_raises(self, dest) -> None:
        # Constructing the session does not open the connection — only
        # entering the context manager does.
        session = dest.streaming_full_load("bronze.foo", "run-1")
        schema = pa.schema([pa.field("id", pa.int64())])
        with pytest.raises(RuntimeError, match="not active"):
            session.append(pa.RecordBatch.from_pydict({"id": [1]}, schema=schema))
