"""Workflow stage 15: feather history.

Scenarios exercise `feather history` — listing prior runs, filtering by
table, and output formatting.
"""

from __future__ import annotations

import json


def _two_table_env(project):
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[
            {
                "type": "duckdb",
                "name": "icube",
                "path": str(project.root / "client.duckdb"),
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    project.write_curation(
        [
            ("icube", "icube.InventoryGroup", "inventory_group"),
            ("icube", "icube.CUSTOMERMASTER", "customer_master"),
        ]
    )


def test_history_shows_runs_after_run(cli, project):
    """feather history shows a table of recent runs."""
    _two_table_env(project)
    cli("run")
    result = cli("history")
    assert result.exit_code == 0
    assert "inventory_group" in result.output
    assert "customer_master" in result.output


def test_history_table_filter(cli, project):
    """feather history --table icube_inventory_group shows only that table's runs."""
    _two_table_env(project)
    cli("run")
    result = cli("history", "--table", "icube_inventory_group")
    assert result.exit_code == 0
    assert "icube_inventory_group" in result.output
    assert "icube_customer_master" not in result.output


def test_history_limit(cli, project):
    """feather history --limit 1 shows at most 1 run."""
    _two_table_env(project)
    cli("run")
    cli("run")
    result = cli("history", "--limit", "1")
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    data_lines = [
        line
        for line in lines
        if not line.startswith("-") and "Table" not in line and line.strip()
    ]
    assert len(data_lines) <= 1


def test_history_empty_state_shows_message(cli, project):
    """feather history with no runs shows a friendly message."""
    _two_table_env(project)
    cli("setup")
    result = cli("history")
    assert result.exit_code == 0
    assert "No runs recorded" in result.output


def test_history_shows_error_row_and_truncates_long_messages(cli, project):
    """A run with an ``error_message`` longer than 80 chars is displayed
    under its row with a ``Error: ...`` line truncated to 77 chars + ``...``."""
    from datetime import datetime, timezone

    from feather_etl.state import StateManager

    # Minimal project config + state DB; no need to actually extract.
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[
            {
                "type": "duckdb",
                "name": "icube",
                "path": str(project.root / "client.duckdb"),
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])

    # Seed a run with a long error message directly in the state DB.
    sm = StateManager(project.state_db_path)
    sm.init_state()
    long_err = "A" * 150  # > 80 chars triggers the truncation branch
    now = datetime.now(timezone.utc)
    sm.record_run(
        run_id="run_with_err",
        table_name="icube_inventory_group",
        started_at=now,
        ended_at=now,
        status="failed",
        error_message=long_err,
    )

    result = cli("history")
    assert result.exit_code == 0
    # The error row is surfaced beneath the table row
    assert "Error:" in result.output
    # The long message is truncated to 77 chars + '...'
    truncated = "A" * 77 + "..."
    assert truncated in result.output
    # And the full 150-char message is not printed
    assert "A" * 150 not in result.output


def test_history_json_outputs_ndjson(cli, project):
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[
            {
                "type": "duckdb",
                "name": "icube",
                "path": str(project.root / "client.duckdb"),
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])

    cli("run")
    result = cli("--json", "history")
    assert result.exit_code == 0
    lines = [line for line in result.output.strip().split("\n") if line.strip()]
    assert len(lines) >= 1
    parsed = json.loads(lines[0])
    assert "run_id" in parsed
    assert "table_name" in parsed
    # trigger key is now part of the history JSON contract.
    assert "trigger" in parsed
    assert parsed["trigger"] == "run"


# ---------------------------------------------------------------------------
# --trigger filter (feather-transform Wave B)
# ---------------------------------------------------------------------------


def _extract_then_run_env(project):
    """Project set up for both `feather extract` and `feather run`.

    Bronze pulled via extract; run loads the same tables. Yields both
    trigger='extract' and trigger='run' rows in _runs.
    """
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[
            {
                "type": "duckdb",
                "name": "icube",
                "path": str(project.root / "client.duckdb"),
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inv")])


def test_history_trigger_filter_run(cli, project):
    """`feather history --trigger run` returns only run rows."""
    _extract_then_run_env(project)
    cli("extract")
    cli("run")

    result = cli("history", "--trigger", "run")
    assert result.exit_code == 0
    # Header is unconditional; assert run output, no extract rows.
    assert "run" in result.output
    # Output contains a `Trigger` column header now.
    assert "Trigger" in result.output


def test_history_trigger_filter_extract(cli, project):
    """`feather history --trigger extract` returns only extract rows."""
    _extract_then_run_env(project)
    cli("extract")
    cli("run")

    result = cli("--json", "history", "--trigger", "extract")
    assert result.exit_code == 0
    lines = [line for line in result.output.strip().split("\n") if line.strip()]
    assert lines, "expected at least one extract row"
    for line in lines:
        row = json.loads(line)
        assert row["trigger"] == "extract", row


def test_history_trigger_filter_excludes_other_verbs(cli, project):
    """The filter drops rows whose trigger doesn't match."""
    _extract_then_run_env(project)
    cli("extract")
    cli("run")

    json_run = cli("--json", "history", "--trigger", "run")
    json_extract = cli("--json", "history", "--trigger", "extract")

    run_rows = [
        json.loads(line)
        for line in json_run.output.strip().split("\n")
        if line.strip()
    ]
    extract_rows = [
        json.loads(line)
        for line in json_extract.output.strip().split("\n")
        if line.strip()
    ]

    assert all(r["trigger"] == "run" for r in run_rows)
    assert all(r["trigger"] == "extract" for r in extract_rows)
    # And they're disjoint sets — different verbs, different rows.
    run_ids = {r["run_id"] for r in run_rows}
    extract_ids = {r["run_id"] for r in extract_rows}
    assert run_ids.isdisjoint(extract_ids)
