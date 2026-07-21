"""Unit tests for _extract_windows state table — durable per-window commit log."""

from __future__ import annotations

import duckdb
from datetime import datetime, timezone
from pathlib import Path

from feather_flow.state import StateManager


def _make_state(tmp_path: Path) -> StateManager:
    state = StateManager(tmp_path / "feather_state.duckdb")
    state.init_state()
    return state


def test_get_committed_windows_empty_initially(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    assert state.get_committed_windows("sales") == set()


def test_record_and_get_committed_window(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    state.record_committed_window(
        table_name="sales",
        window_key="2026-05-12",
        window_start=datetime(2026, 5, 12, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
        rows_loaded=12345,
        transport_used="pyodbc",
        run_id="run_001",
    )
    assert state.get_committed_windows("sales") == {"2026-05-12"}


def test_record_committed_window_replaces_on_duplicate_key(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    state.record_committed_window(
        table_name="sales",
        window_key="2026-05-12",
        window_start=datetime(2026, 5, 12, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
        rows_loaded=100,
        transport_used="pyodbc",
        run_id="run_001",
    )
    state.record_committed_window(
        table_name="sales",
        window_key="2026-05-12",
        window_start=datetime(2026, 5, 12, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
        rows_loaded=200,  # new value
        transport_used="pyodbc",
        run_id="run_002",
    )
    con = duckdb.connect(str(state.path))
    try:
        row = con.execute(
            "SELECT rows_loaded, run_id FROM _extract_windows "
            "WHERE table_name = 'sales' AND window_key = '2026-05-12'"
        ).fetchone()
        assert row == (200, "run_002")
    finally:
        con.close()


def test_clear_windows_for_table_only_affects_named_table(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    for tbl in ("sales", "customers"):
        state.record_committed_window(
            table_name=tbl,
            window_key="all",
            window_start=None,
            window_end=None,
            rows_loaded=1,
            transport_used="pyodbc",
            run_id="r",
        )
    state.clear_windows_for_table("sales")
    assert state.get_committed_windows("sales") == set()
    assert state.get_committed_windows("customers") == {"all"}


def test_window_start_end_nullable_for_single_window(tmp_path: Path) -> None:
    """`window_strategy: single` has no temporal bounds — start/end are NULL."""
    state = _make_state(tmp_path)
    state.record_committed_window(
        table_name="dim_branch",
        window_key="all",
        window_start=None,
        window_end=None,
        rows_loaded=16,
        transport_used="pyodbc",
        run_id="r",
    )
    assert state.get_committed_windows("dim_branch") == {"all"}
