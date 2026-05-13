"""#62 acceptance — crash mid-extract leaves bronze + commit log consistent;
next run completes with zero duplicates.

These tests drive load_batched_append directly (not run_extract) so the
crash is reliably simulatable. The single-window planner means
'next window to run' degenerates to 'the same window again' here — #63
extends this to multi-window scenarios."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from feather_etl.destinations.duckdb import DuckDBDestination
from feather_etl.extract_windows import WindowSpec
from feather_etl.state import StateManager


def _setup(tmp_path: Path) -> tuple[DuckDBDestination, StateManager]:
    dest = DuckDBDestination(path=tmp_path / "data.duckdb")
    dest.setup_schemas()
    state = StateManager(tmp_path / "state.duckdb")
    state.init_state()
    return dest, state


def test_crash_mid_window_leaves_no_partial_commit(tmp_path: Path) -> None:
    """Iterator raises mid-window → bronze + _extract_windows both untouched
    for this window (either rolled back, or never reached the commit point)."""
    dest, state = _setup(tmp_path)
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)

    def half_then_crash():
        yield pa.RecordBatch.from_pylist([{"id": 1, "name": "a", "day": "2026-05-12"}])
        yield pa.RecordBatch.from_pylist([{"id": 2, "name": "b", "day": "2026-05-12"}])
        raise RuntimeError("transport disconnected")

    with pytest.raises(RuntimeError, match="disconnected"):
        dest.load_batched_append(
            "bronze.foo", spec, half_then_crash(), "r1", state, "pyodbc"
        )

    # Commit log untouched — window is NOT marked committed.
    assert state.get_committed_windows("bronze.foo") == set()

    # Bronze either doesn't exist or has zero rows in this window's predicate.
    con = duckdb.connect(str(dest.path))
    try:
        existed = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'bronze' AND table_name = 'foo'"
        ).fetchone()[0]
        if existed:
            n = con.execute(
                "SELECT COUNT(*) FROM bronze.foo WHERE day = '2026-05-12'"
            ).fetchone()[0]
            assert n == 0
    finally:
        con.close()


def test_resume_completes_after_crash(tmp_path: Path) -> None:
    """After a crashed first attempt, the second attempt completes the window
    with zero duplicates — the DELETE-WHERE-predicate in the new write cleans
    any orphan rows from the crashed attempt."""
    dest, state = _setup(tmp_path)
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)

    def crashing():
        yield pa.RecordBatch.from_pylist([{"id": 1, "name": "a", "day": "2026-05-12"}])
        raise RuntimeError("disconnected")

    with pytest.raises(RuntimeError):
        dest.load_batched_append("bronze.foo", spec, crashing(), "r1", state, "pyodbc")

    def clean():
        yield pa.RecordBatch.from_pylist(
            [
                {"id": 1, "name": "a", "day": "2026-05-12"},
                {"id": 2, "name": "b", "day": "2026-05-12"},
            ]
        )

    result = dest.load_batched_append(
        "bronze.foo", spec, clean(), "r2", state, "pyodbc"
    )

    assert result.rows_loaded == 2
    assert state.get_committed_windows("bronze.foo") == {"2026-05-12"}
    con = duckdb.connect(str(dest.path))
    try:
        rows = con.execute("SELECT id, name FROM bronze.foo ORDER BY id").fetchall()
    finally:
        con.close()
    assert rows == [(1, "a"), (2, "b")]
