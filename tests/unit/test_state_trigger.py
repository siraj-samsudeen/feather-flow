"""Schema migration + trigger-column behaviour for ``_runs``.

Covers tasks 4.1, 4.3, and 4.4 of the feather-transform change: the
``_runs.trigger`` column on fresh DBs, the idempotent ALTER-on-connect
migration for legacy DBs, the validator at ``record_run``, and the new
``trigger`` filter on ``get_history``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from feather_flow.state import VALID_TRIGGERS, StateManager


def _columns_of(state_path: Path, table: str) -> list[str]:
    """Return column names for a given state-DB table (order-preserving)."""
    con = duckdb.connect(str(state_path), read_only=True)
    try:
        rows = con.execute(f"DESCRIBE {table}").fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 4.1 — fresh DB has the column; migration is idempotent on legacy DBs
# ---------------------------------------------------------------------------


def test_fresh_state_db_has_trigger_column(tmp_path: Path):
    """A brand-new state DB's ``_runs`` table includes ``trigger``."""
    sm = StateManager(tmp_path / "state.duckdb")
    sm.init_state()
    cols = _columns_of(tmp_path / "state.duckdb", "_runs")
    assert "trigger" in cols, f"Expected 'trigger' in _runs columns; got {cols}"


def test_migration_adds_trigger_to_legacy_runs_table(tmp_path: Path):
    """A legacy ``_runs`` table (no trigger column) gains it on first connect.

    Simulates the upgrade path: create ``_runs`` by hand without ``trigger``,
    then have ``StateManager.init_state()`` run — the ALTER must add it.
    """
    state_path = tmp_path / "state.duckdb"
    # Build a legacy _runs table (no trigger column) directly via duckdb.
    con = duckdb.connect(str(state_path))
    con.execute("""
        CREATE TABLE _runs (
            run_id VARCHAR PRIMARY KEY,
            table_name VARCHAR,
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            duration_sec DOUBLE,
            status VARCHAR,
            rows_extracted INTEGER,
            rows_loaded INTEGER,
            rows_skipped INTEGER,
            error_message VARCHAR,
            watermark_before VARCHAR,
            watermark_after VARCHAR,
            freshness_max_ts TIMESTAMP,
            schema_changes JSON
        )
    """)
    # Seed a pre-upgrade row so we can later confirm trigger=NULL on it.
    con.execute(
        "INSERT INTO _runs (run_id, table_name, status) VALUES (?, ?, ?)",
        ["legacy-1", "old_table", "success"],
    )
    con.close()

    # Pre-migration: no trigger column.
    assert "trigger" not in _columns_of(state_path, "_runs")

    # First StateManager connect runs the idempotent ALTER.
    sm = StateManager(state_path)
    sm.init_state()
    assert "trigger" in _columns_of(state_path, "_runs")

    # Second open must be a no-op — running upgraded code twice is safe.
    sm.init_state()
    assert "trigger" in _columns_of(state_path, "_runs")

    # Legacy row retains trigger=NULL — no backfill.
    con = duckdb.connect(str(state_path), read_only=True)
    try:
        row = con.execute(
            "SELECT trigger FROM _runs WHERE run_id = 'legacy-1'"
        ).fetchone()
        assert row[0] is None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 4.3 — validator at record_run
# ---------------------------------------------------------------------------


def test_valid_triggers_constant_holds_four_values():
    """The accepted set is exactly ``{run, extract, transform, schedule}``."""
    assert VALID_TRIGGERS == {"run", "extract", "transform", "schedule"}


@pytest.mark.parametrize("trigger", ["run", "extract", "transform", "schedule"])
def test_record_run_accepts_valid_trigger(tmp_path: Path, trigger: str):
    sm = StateManager(tmp_path / "state.duckdb")
    sm.init_state()
    now = datetime.now(timezone.utc)
    sm.record_run(
        run_id=f"r-{trigger}",
        table_name="t",
        started_at=now,
        ended_at=now,
        status="success",
        trigger=trigger,
    )
    rows = sm.get_history(trigger=trigger)
    assert len(rows) == 1
    assert rows[0]["trigger"] == trigger


def test_record_run_rejects_unknown_trigger(tmp_path: Path):
    sm = StateManager(tmp_path / "state.duckdb")
    sm.init_state()
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="Invalid trigger value"):
        sm.record_run(
            run_id="r-bad",
            table_name="t",
            started_at=now,
            ended_at=now,
            status="success",
            trigger="cache",
        )


def test_record_run_allows_none_trigger(tmp_path: Path):
    """``trigger=None`` is permitted — represents legacy / unknown verb."""
    sm = StateManager(tmp_path / "state.duckdb")
    sm.init_state()
    now = datetime.now(timezone.utc)
    sm.record_run(
        run_id="r-null",
        table_name="t",
        started_at=now,
        ended_at=now,
        status="success",
        trigger=None,
    )
    rows = sm.get_history()
    assert len(rows) == 1
    assert rows[0]["trigger"] is None


# ---------------------------------------------------------------------------
# 4.4 — load_history(trigger=...) filter
# ---------------------------------------------------------------------------


def _seed_mixed_history(state_path: Path) -> StateManager:
    sm = StateManager(state_path)
    sm.init_state()
    now = datetime.now(timezone.utc)
    sm.record_run(
        run_id="r-run",
        table_name="t1",
        started_at=now,
        ended_at=now,
        status="success",
        trigger="run",
    )
    sm.record_run(
        run_id="r-extract",
        table_name="t1",
        started_at=now,
        ended_at=now,
        status="success",
        trigger="extract",
    )
    # Simulate legacy row by inserting directly with trigger NULL.
    con = duckdb.connect(str(state_path))
    con.execute(
        "INSERT INTO _runs (run_id, table_name, started_at, ended_at, "
        "duration_sec, status, trigger) VALUES (?, ?, ?, ?, ?, ?, NULL)",
        ["r-legacy", "t1", now, now, 0.0, "success"],
    )
    con.close()
    return sm


def test_load_history_with_trigger_returns_only_matching_rows(tmp_path: Path):
    from feather_flow.history import load_history

    state_path = tmp_path / "state.duckdb"
    _seed_mixed_history(state_path)

    rows = load_history(state_path, trigger="run")
    assert len(rows) == 1
    assert rows[0]["run_id"] == "r-run"
    assert rows[0]["trigger"] == "run"


def test_load_history_unfiltered_includes_null_trigger_rows(tmp_path: Path):
    from feather_flow.history import load_history

    state_path = tmp_path / "state.duckdb"
    _seed_mixed_history(state_path)

    rows = load_history(state_path)
    run_ids = {r["run_id"] for r in rows}
    assert run_ids == {"r-run", "r-extract", "r-legacy"}
    # The legacy row has trigger=NULL.
    legacy = next(r for r in rows if r["run_id"] == "r-legacy")
    assert legacy["trigger"] is None
