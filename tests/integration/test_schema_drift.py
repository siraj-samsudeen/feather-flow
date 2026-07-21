"""Integration: schema drift detection — pipeline re-runs after ALTER TABLE
on source DB.
"""

from __future__ import annotations

import json
import os
import time

import duckdb

import logging

from feather_flow.config import load_config
from feather_flow.pipeline import run_table
from feather_flow.state import StateManager
from tests.helpers import make_curation_entry, write_curation


def test_first_run_saves_baseline(project, sample_erp_db):
    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(sample_erp_db)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.customers",
                "customers",
                primary_key=["customer_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)

    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "success"

    sm = StateManager(project.state_db_path)
    snapshot = sm.get_schema_snapshot("src_customers")
    assert snapshot is not None
    assert len(snapshot) > 0


def test_schema_changes_logged_in_runs(project, sample_erp_db):
    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(sample_erp_db)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.customers",
                "customers",
                primary_key=["customer_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)

    run_table(cfg, cfg.tables[0], project.root)

    con = duckdb.connect(str(sample_erp_db))
    con.execute("ALTER TABLE erp.customers ADD COLUMN phone VARCHAR")
    con.close()

    time.sleep(0.1)
    os.utime(str(sample_erp_db), None)

    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "success"

    sm = StateManager(project.state_db_path)
    con = sm._connect()
    rows = con.execute(
        "SELECT schema_changes FROM _runs WHERE table_name = 'src_customers' "
        "AND schema_changes IS NOT NULL"
    ).fetchall()
    con.close()
    assert len(rows) >= 1
    changes = json.loads(rows[0][0])
    assert "added" in changes
    assert any("phone" in str(col) for col in changes["added"])


def test_schema_drift_check_failure_is_caught_and_logged(
    project, sample_erp_db, monkeypatch, caplog
):
    """If ``detect_drift`` (or anything inside the schema-drift try block)
    raises, the outer ``except Exception`` in run_table logs a warning and
    the extract continues normally. (pipeline.py:304-305)"""
    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(sample_erp_db)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.customers",
                "customers",
                primary_key=["customer_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)

    # First run establishes a schema snapshot.
    first = run_table(cfg, cfg.tables[0], project.root)
    assert first.status == "success"

    # Make detect_drift raise on the next run. run_table imports detect_drift
    # locally at call time, so patch the definition in schema_drift.
    import feather_flow.schema_drift as schema_drift_mod

    def _boom(*_a, **_kw):
        raise RuntimeError("synthetic drift-check failure")

    monkeypatch.setattr(schema_drift_mod, "detect_drift", _boom)

    # Force the source file to look different so change-detection fires.
    time.sleep(0.1)
    con = duckdb.connect(str(sample_erp_db))
    con.execute(
        "INSERT INTO erp.customers (customer_id, name, city) "
        "VALUES (99, 'drift-test', 'Nowhere')"
    )
    con.close()
    os.utime(str(sample_erp_db), None)

    with caplog.at_level(logging.WARNING, logger="feather_flow.pipeline"):
        second = run_table(cfg, cfg.tables[0], project.root)

    # Extract still succeeds despite the failed drift check.
    assert second.status == "success"
    assert "Schema drift check failed" in caplog.text
    assert "synthetic drift-check failure" in caplog.text
