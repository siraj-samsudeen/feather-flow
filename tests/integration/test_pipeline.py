"""Integration: feather_flow.pipeline — run_table and run_all orchestration across sources, destinations, and state."""

from __future__ import annotations

import os
import time

import duckdb

from feather_flow.config import load_config
from feather_flow.pipeline import run_all, run_table
from feather_flow.state import StateManager


def _setup_project(project):
    """Copy the client fixture, write a two-table config/curation, return cfg.

    Shared setup for every test in this module: mirrors the original
    ``setup_env`` local fixture from ``tests/test_pipeline.py``.
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
    project.write_curation(
        [
            ("icube", "icube.InventoryGroup", "inventory_group"),
            ("icube", "icube.CUSTOMERMASTER", "customer_master"),
        ]
    )


def test_extracts_and_loads(project):
    _setup_project(project)
    cfg = load_config(project.config_path)

    result = run_table(cfg, cfg.tables[0], project.root)

    assert result.status == "success"
    assert result.rows_loaded == 66  # InventoryGroup has 66 rows


def test_records_state(project):
    _setup_project(project)
    cfg = load_config(project.config_path)
    run_table(cfg, cfg.tables[0], project.root)

    sm = StateManager(project.state_db_path)
    status = sm.get_status()
    assert len(status) == 1
    assert status[0]["status"] == "success"


def test_data_has_etl_metadata(project):
    _setup_project(project)
    cfg = load_config(project.config_path)
    run_table(cfg, cfg.tables[0], project.root)

    row = project.query(
        "SELECT _etl_loaded_at, _etl_run_id FROM bronze.icube_inventory_group LIMIT 1"
    )[0]
    assert row[0] is not None
    assert "icube_inventory_group" in row[1]


def test_runs_all_tables(project):
    _setup_project(project)
    cfg = load_config(project.config_path)

    results = run_all(cfg)

    assert len(results) == 2
    assert all(r.status == "success" for r in results)


def test_failed_table_doesnt_stop_others(project):
    _setup_project(project)
    cfg = load_config(project.config_path)
    # Point one table to nonexistent source table
    cfg.tables[0].source_table = "icube.NONEXISTENT"

    results = run_all(cfg)

    statuses = {r.table_name: r.status for r in results}
    assert statuses["icube_inventory_group"] == "failure"
    assert statuses["icube_customer_master"] == "success"


def test_run_all_does_not_write_validation_json(project):
    """L-1: run_all() should NOT write validation JSON -- CLI owns that."""
    _setup_project(project)
    cfg = load_config(project.config_path)

    vj_path = project.config_path.parent / "feather_validation.json"
    vj_path.unlink(missing_ok=True)

    run_all(cfg)

    assert not vj_path.exists()


def test_first_run_always_extracts(project):
    """First run with no prior watermark always extracts."""
    _setup_project(project)
    cfg = load_config(project.config_path)

    result = run_table(cfg, cfg.tables[0], project.root)

    assert result.status == "success"
    assert result.rows_loaded > 0


def test_watermark_populated_after_success(project):
    """After successful run, watermark has mtime and hash."""
    _setup_project(project)
    cfg = load_config(project.config_path)
    run_table(cfg, cfg.tables[0], project.root)

    sm = StateManager(project.state_db_path)
    wm = sm.read_watermark("icube_inventory_group")
    assert wm is not None
    assert wm["last_file_mtime"] is not None
    assert wm["last_file_hash"] is not None


def test_second_run_skips_unchanged(project):
    """Second run on unchanged source -> all tables skipped."""
    _setup_project(project)
    cfg = load_config(project.config_path)

    run_all(cfg)
    results2 = run_all(cfg)

    assert all(r.status == "skipped" for r in results2)


def test_modified_source_reextracts(project):
    """Modify source file -> next run extracts again."""
    _setup_project(project)
    cfg = load_config(project.config_path)

    run_all(cfg)

    time.sleep(0.05)
    source_db = project.root / "client.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute(
        "INSERT INTO icube.InventoryGroup SELECT * FROM icube.InventoryGroup LIMIT 1"
    )
    con.close()

    results2 = run_all(cfg)
    assert all(r.status == "success" for r in results2)


def test_touch_source_skips(project):
    """Touch source file (mtime changes, content identical) -> skipped."""
    _setup_project(project)
    cfg = load_config(project.config_path)

    run_all(cfg)

    time.sleep(0.05)
    os.utime(project.root / "client.duckdb", None)

    results2 = run_all(cfg)
    assert all(r.status == "skipped" for r in results2)


def test_skipped_run_recorded_in_state(project):
    """Skipped runs are recorded in _runs table."""
    _setup_project(project)
    cfg = load_config(project.config_path)

    run_all(cfg)
    run_all(cfg)

    sm = StateManager(project.state_db_path)
    status = sm.get_status()
    assert all(s["status"] == "skipped" for s in status)


def test_failed_extraction_doesnt_update_watermark(project):
    """Failed extraction should not populate mtime/hash in watermark."""
    _setup_project(project)
    cfg = load_config(project.config_path)
    cfg.tables[0].source_table = "icube.NONEXISTENT"

    run_table(cfg, cfg.tables[0], project.root)

    sm = StateManager(project.state_db_path)
    wm = sm.read_watermark("icube_inventory_group")
    if wm is not None:
        assert wm["last_value"] is None
        assert wm["last_run_at"] is None
        assert wm["retry_count"] == 1
