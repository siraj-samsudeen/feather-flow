"""Workflow stage 04: extract — feather setup + feather run happy path.

Scenarios here exercise the full extraction workflow: `feather setup`
creates the state + destination DBs and schemas, then `feather run`
extracts every configured table. Also covers table filtering (--table)
and auto-creation of state/data DBs when `run` is invoked without a
prior `setup`.

One test (`test_full_onboarding_flow`) chains every CLI command in
sequence — the "happy path user journey" end-to-end.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import yaml

from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


# ---------------------------------------------------------------------------
# Helpers (inlined from tests/commands/conftest.py cli_env / two_table_env)
# ---------------------------------------------------------------------------


def _one_table_project(project) -> Path:
    """Set up a single-table curation project (icube.InventoryGroup)."""
    client_db = project.root / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        destination={"path": str(project.data_db_path)},
    )
    project.write_curation(
        [("icube", "icube.InventoryGroup", "inventory_group")],
    )
    return project.config_path


def _two_table_project(project) -> Path:
    """Set up a two-table curation project (InventoryGroup + CUSTOMERMASTER)."""
    client_db = project.root / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        destination={"path": str(project.data_db_path)},
    )
    project.write_curation(
        [
            ("icube", "icube.InventoryGroup", "inventory_group"),
            ("icube", "icube.CUSTOMERMASTER", "customer_master"),
        ],
    )
    return project.config_path


# ---------------------------------------------------------------------------
# feather setup (formerly TestSetup)
# ---------------------------------------------------------------------------


def test_setup_creates_state_and_schemas(project, cli):
    _one_table_project(project)
    result = cli("setup")
    assert result.exit_code == 0
    assert (project.root / "feather_state.duckdb").exists()


# ---------------------------------------------------------------------------
# feather run — happy path (formerly TestRun)
# ---------------------------------------------------------------------------


def test_run_extracts_tables(project, cli):
    _one_table_project(project)
    result = cli("run")
    assert result.exit_code == 0
    assert "success" in result.output.lower()


def test_run_with_bad_table_shows_failure(project, cli):
    client_db = project.root / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [make_curation_entry("icube", "icube.NONEXISTENT", "bad_table")],
    )
    result = cli("run")
    assert result.exit_code == 1
    assert "failure" in result.output.lower()


def test_backoff_skipped_table_exits_nonzero(project, cli):
    """Second run after partial failure should exit non-zero (backoff-skipped)."""
    client_db = project.root / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [
            make_curation_entry("icube", "icube.SALESINVOICE", "good_table"),
            make_curation_entry("icube", "nonexistent.NOPE", "bad_table"),
        ],
    )

    result1 = cli("run")
    assert result1.exit_code == 1

    result2 = cli("run")
    assert result2.exit_code == 1, (
        f"Expected non-zero exit when table is backoff-skipped, got 0. "
        f"Output: {result2.output}"
    )


def test_run_json_outputs_ndjson(project, cli):
    _one_table_project(project)
    result = cli("--json", "run")
    assert result.exit_code == 0
    lines = [line for line in result.output.strip().split("\n") if line.strip()]
    assert len(lines) >= 1
    parsed = json.loads(lines[0])
    assert "table_name" in parsed
    assert "status" in parsed


# ---------------------------------------------------------------------------
# --table filter (formerly TestTableFilter)
# ---------------------------------------------------------------------------


def test_table_filter_extracts_only_matching_table(project, cli):
    """--table icube_inventory_group should extract only that table."""
    _two_table_project(project)
    result = cli("run", "--table", "icube_inventory_group")
    assert result.exit_code == 0
    assert "icube_inventory_group" in result.output
    assert "icube_customer_master" not in result.output


def test_table_filter_nonexistent_table_exits_with_error(project, cli):
    """--table nonexistent should exit with code 1 and show error."""
    _two_table_project(project)
    result = cli("run", "--table", "nonexistent")
    assert result.exit_code == 1
    assert (
        "nonexistent" in result.output.lower()
        or "nonexistent" in (result.stderr or "").lower()
    )


def test_no_table_flag_extracts_all_tables(project, cli):
    """Without --table, all configured tables are extracted."""
    _two_table_project(project)
    result = cli("run")
    assert result.exit_code == 0
    assert "icube_inventory_group" in result.output
    assert "icube_customer_master" in result.output


# ---------------------------------------------------------------------------
# Auto-create (formerly TestRunAutoCreates)
# ---------------------------------------------------------------------------


def test_run_without_setup_auto_creates_state_and_data(project, cli):
    """UX-7: feather run creates state/data DBs automatically. setup is optional."""
    _one_table_project(project)
    result = cli("run")
    assert result.exit_code == 0
    assert (project.root / "feather_state.duckdb").exists()
    assert (project.root / "feather_data.duckdb").exists()


# ---------------------------------------------------------------------------
# Full onboarding god test (formerly tests/test_e2e.py::test_full_onboarding_flow)
# ---------------------------------------------------------------------------


def test_full_onboarding_flow(project, cli, monkeypatch):
    """init -> validate -> discover -> setup -> run -> status -> run again."""
    # --- 1. feather init ---
    project_dir = project.root / "client-test"
    result = cli("init", str(project_dir), config=False)
    assert result.exit_code == 0, result.output
    assert (project_dir / "feather.yaml").exists()
    assert (project_dir / "pyproject.toml").exists()
    assert (project_dir / ".gitignore").exists()
    assert (project_dir / ".env.example").exists()

    # --- 2. Edit feather.yaml (simulating operator) ---
    client_db = project_dir / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)

    config = {
        "sources": [{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        "destination": {"path": str(project_dir / "feather_data.duckdb")},
    }
    config_path = project_dir / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))

    write_curation(
        project_dir,
        [
            make_curation_entry("icube", "icube.SALESINVOICE", "sales_invoice"),
            make_curation_entry("icube", "icube.CUSTOMERMASTER", "customer_master"),
            make_curation_entry("icube", "icube.InventoryGroup", "inventory_group"),
        ],
    )

    # --- 3. feather validate ---
    result = cli("validate", config=config_path)
    assert result.exit_code == 0, result.output
    vj_path = project_dir / "feather_validation.json"
    assert vj_path.exists()

    # --- 4. feather discover ---
    monkeypatch.chdir(project_dir)
    import feather_etl.commands.discover as discover_cmd

    monkeypatch.setattr(discover_cmd, "serve_and_open", lambda *args, **kwargs: None)
    result = cli("discover", config=config_path)
    assert result.exit_code == 0, result.output
    assert "discovered" in result.output

    schema_files = list(project_dir.glob("schema_*.json"))
    assert len(schema_files) == 1, f"Expected one schema file, found: {schema_files}"
    payload = json.loads(schema_files[0].read_text())
    table_names = [entry["table_name"] for entry in payload]
    assert any("SALESINVOICE" in t for t in table_names)
    assert any("CUSTOMERMASTER" in t for t in table_names)
    assert any("InventoryGroup" in t for t in table_names)

    # --- 5. feather setup ---
    result = cli("setup", config=config_path)
    assert result.exit_code == 0, result.output
    assert (project_dir / "feather_state.duckdb").exists()

    # --- 6. feather run ---
    result = cli("run", config=config_path)
    assert result.exit_code == 0, result.output
    assert "3/3 tables extracted" in result.output

    # Verify bronze tables exist with correct data
    data_db = str(project_dir / "feather_data.duckdb")
    con = duckdb.connect(data_db, read_only=True)

    si_count = con.execute(
        "SELECT COUNT(*) FROM bronze.icube_sales_invoice"
    ).fetchone()[0]
    assert si_count == 11676

    cm_count = con.execute(
        "SELECT COUNT(*) FROM bronze.icube_customer_master"
    ).fetchone()[0]
    assert cm_count == 1339

    ig_count = con.execute(
        "SELECT COUNT(*) FROM bronze.icube_inventory_group"
    ).fetchone()[0]
    assert ig_count == 66

    # Verify ETL metadata columns
    row = con.execute(
        "SELECT _etl_loaded_at, _etl_run_id FROM bronze.icube_sales_invoice LIMIT 1"
    ).fetchone()
    assert row[0] is not None
    assert "icube_sales_invoice" in row[1]
    con.close()

    # --- 7. feather status ---
    result = cli("status", config=config_path)
    assert result.exit_code == 0, result.output
    assert "icube_sales_invoice" in result.output
    assert "icube_customer_master" in result.output
    assert "icube_inventory_group" in result.output
    assert "success" in result.output

    # --- 8. Second feather run (change detection -> skip unchanged) ---
    result = cli("run", config=config_path)
    assert result.exit_code == 0, result.output
    assert "skipped (unchanged)" in result.output
    assert "3 skipped" in result.output

    # Verify feather_validation.json was written
    assert vj_path.exists()
