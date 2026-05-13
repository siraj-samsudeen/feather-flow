"""Unit tests for DuckDBDestination.load_batched_append (#62).

Replaces the deleted test_streaming_full_load.py (deleted in Task 6). Covers:
  - CREATE TABLE IF NOT EXISTS on first window
  - INSERT semantics for subsequent batches in a window
  - DELETE WHERE <predicate> before INSERT (idempotency within window)
  - _extract_windows row written atomically with bronze INSERT
  - Failure inside iterator → bronze unchanged, _extract_windows unchanged
  - Empty iterator (one zero-row batch) → table created (if first window),
    zero rows inserted, window still marked committed
  - run_id metadata propagated to _etl_run_id
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from feather_etl.destinations.duckdb import DuckDBDestination
from feather_etl.extract_windows import WindowSpec
from feather_etl.state import StateManager


def _batches(*tables: pa.Table):
    for t in tables:
        yield from t.to_batches()


def _table(rows: list[dict], schema: pa.Schema | None = None) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=schema)


def _make_dest_and_state(tmp_path: Path) -> tuple[DuckDBDestination, StateManager]:
    dest = DuckDBDestination(path=tmp_path / "feather_data.duckdb")
    dest.setup_schemas()
    state = StateManager(tmp_path / "feather_state.duckdb")
    state.init_state()
    return dest, state


def _all_rows(dest: DuckDBDestination, table: str) -> list[tuple]:
    con = duckdb.connect(str(dest.path))
    try:
        return con.execute(f"SELECT id, name FROM {table} ORDER BY id").fetchall()
    finally:
        con.close()


def test_first_window_creates_table_and_inserts(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("all", "1=1", None, None)
    result = dest.load_batched_append(
        table="bronze.foo",
        window=spec,
        batches=_batches(_table([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])),
        run_id="r1",
        state=state,
        transport_used="pyodbc",
    )
    assert result.rows_loaded == 2
    assert result.committed is True
    assert _all_rows(dest, "bronze.foo") == [(1, "a"), (2, "b")]
    assert state.get_committed_windows("bronze.foo") == {"all"}


def test_window_delete_predicate_idempotent_rerun(tmp_path: Path) -> None:
    """Re-running the same window replaces, not duplicates."""
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)
    tbl = _table([{"id": 1, "name": "a", "day": "2026-05-12"}])

    dest.load_batched_append("bronze.foo", spec, _batches(tbl), "r1", state, "pyodbc")
    dest.load_batched_append("bronze.foo", spec, _batches(tbl), "r2", state, "pyodbc")

    con = duckdb.connect(str(dest.path))
    try:
        n = con.execute("SELECT COUNT(*) FROM bronze.foo").fetchone()[0]
    finally:
        con.close()
    assert n == 1


def test_two_windows_accumulate(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    w1 = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)
    w2 = WindowSpec("2026-05-13", "day = '2026-05-13'", None, None)

    dest.load_batched_append(
        "bronze.foo",
        w1,
        _batches(_table([{"id": 1, "name": "a", "day": "2026-05-12"}])),
        "r",
        state,
        "pyodbc",
    )
    dest.load_batched_append(
        "bronze.foo",
        w2,
        _batches(_table([{"id": 2, "name": "b", "day": "2026-05-13"}])),
        "r",
        state,
        "pyodbc",
    )
    assert state.get_committed_windows("bronze.foo") == {"2026-05-12", "2026-05-13"}
    con = duckdb.connect(str(dest.path))
    try:
        n = con.execute("SELECT COUNT(*) FROM bronze.foo").fetchone()[0]
    finally:
        con.close()
    assert n == 2


def test_failure_mid_window_leaves_state_clean(tmp_path: Path) -> None:
    """Iterator raises → no bronze rows for this window, no _extract_windows row."""
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)

    def boom():
        yield from _table([{"id": 1, "name": "a", "day": "2026-05-12"}]).to_batches()
        raise RuntimeError("simulated transport failure")

    with pytest.raises(RuntimeError, match="simulated"):
        dest.load_batched_append(
            "bronze.foo",
            spec,
            boom(),
            "r1",
            state,
            "pyodbc",
        )

    assert state.get_committed_windows("bronze.foo") == set()
    # Bronze may or may not exist; if it does it has zero rows from this aborted window.
    con = duckdb.connect(str(dest.path))
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='bronze' AND table_name='foo'"
        ).fetchone()[0]
        if n == 1:
            assert con.execute("SELECT COUNT(*) FROM bronze.foo").fetchone()[0] == 0
    finally:
        con.close()


def test_empty_iterator_records_zero_row_window(tmp_path: Path) -> None:
    """A window legitimately with zero source rows still commits — it's done."""
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("empty-day", "day = '1999-01-01'", None, None)
    schema = pa.schema(
        [("id", pa.int64()), ("name", pa.string()), ("day", pa.string())]
    )

    def empty_iter():
        yield pa.RecordBatch.from_pylist([], schema=schema)

    result = dest.load_batched_append(
        "bronze.foo",
        spec,
        empty_iter(),
        "r1",
        state,
        "pyodbc",
    )
    assert result.rows_loaded == 0
    assert result.committed is True
    assert state.get_committed_windows("bronze.foo") == {"empty-day"}


def test_run_id_propagated_to_etl_metadata(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("all", "1=1", None, None)
    dest.load_batched_append(
        "bronze.foo",
        spec,
        _batches(_table([{"id": 1, "name": "a"}])),
        "run_42",
        state,
        "pyodbc",
    )
    con = duckdb.connect(str(dest.path))
    try:
        row = con.execute("SELECT _etl_run_id FROM bronze.foo").fetchone()
    finally:
        con.close()
    assert row[0] == "run_42"


def test_rejects_unqualified_table_name(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("all", "1=1", None, None)
    with pytest.raises(ValueError, match="expects schema.table"):
        dest.load_batched_append(
            "foo",
            spec,
            iter(()),
            "r1",
            state,
            "pyodbc",
        )


def test_zero_batches_with_no_prior_table_raises(tmp_path: Path) -> None:
    """Truly empty iterator and bronze table doesn't exist → RuntimeError, no state written."""
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("all", "1=1", None, None)

    with pytest.raises(RuntimeError, match="cannot infer schema"):
        dest.load_batched_append(
            "bronze.foo",
            spec,
            iter(()),
            "r1",
            state,
            "pyodbc",
        )

    assert state.get_committed_windows("bronze.foo") == set()
