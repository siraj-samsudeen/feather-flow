"""Integration: feather_flow.status.load_status + feather_flow.history.load_history.

Both are thin orchestrators over StateManager; grouped here since they share
shape (read state DB, construct a DTO, surface to a caller — no writes).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


def _make_state_with_runs(state_path: Path) -> None:
    """Create a state DB and insert run rows for two tables."""
    from feather_flow.state import StateManager

    sm = StateManager(state_path)
    sm.init_state()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sm.record_run(
        run_id="run-1",
        table_name="orders",
        started_at=now,
        ended_at=now,
        status="success",
        rows_loaded=10,
    )
    sm.record_run(
        run_id="run-2",
        table_name="customers",
        started_at=now,
        ended_at=now,
        status="success",
        rows_loaded=5,
    )


# ---------------------------------------------------------------------------
# status.load_status
# ---------------------------------------------------------------------------


def test_status_returns_rows_for_each_table(project):
    from feather_flow.status import load_status

    state_path = project.root / "feather_state.duckdb"
    _make_state_with_runs(state_path)

    rows = load_status(state_path)

    assert {r["table_name"] for r in rows} == {"orders", "customers"}


def test_status_returns_empty_list_when_no_runs(project):
    from feather_flow.state import StateManager
    from feather_flow.status import load_status

    state_path = project.root / "feather_state.duckdb"
    sm = StateManager(state_path)
    sm.init_state()

    rows = load_status(state_path)

    assert rows == []


def test_status_raises_state_db_missing_when_no_db(project):
    from feather_flow.exceptions import StateDBMissingError
    from feather_flow.status import load_status

    with pytest.raises(StateDBMissingError):
        load_status(project.root / "missing.duckdb")


# ---------------------------------------------------------------------------
# history.load_history
# ---------------------------------------------------------------------------


def test_history_returns_rows_from_state_db(project):
    from feather_flow.history import load_history

    state_path = project.root / "feather_state.duckdb"
    _make_state_with_runs(state_path)

    rows = load_history(state_path)

    assert len(rows) == 2
    assert {r["table_name"] for r in rows} == {"orders", "customers"}
    assert all(r["status"] == "success" for r in rows)


def test_history_filters_by_table_name(project):
    from feather_flow.history import load_history

    state_path = project.root / "feather_state.duckdb"
    _make_state_with_runs(state_path)

    rows = load_history(state_path, table="orders")

    assert len(rows) == 1
    assert rows[0]["table_name"] == "orders"


def test_history_respects_limit(project):
    from feather_flow.history import load_history

    state_path = project.root / "feather_state.duckdb"
    _make_state_with_runs(state_path)

    rows = load_history(state_path, limit=1)

    assert len(rows) == 1


def test_history_raises_state_db_missing_when_no_db(project):
    from feather_flow.exceptions import StateDBMissingError
    from feather_flow.history import load_history

    with pytest.raises(StateDBMissingError):
        load_history(project.root / "missing.duckdb")
