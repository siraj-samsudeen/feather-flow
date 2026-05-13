"""Integration: transforms + pipeline — rebuild_materialized_gold invoked
from pipeline.run_all.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from feather_etl.config import load_config
from feather_etl.pipeline import run_all
from tests.helpers import make_curation_entry, write_curation


def _write_sql(base: Path, schema: str, name: str, content: str) -> Path:
    """Write a .sql file under base/transforms/{schema}/{name}.sql."""
    d = base / "transforms" / schema
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.sql"
    p.write_text(content)
    return p


def test_run_rebuilds_materialized_gold(tmp_path: Path):
    source_db = tmp_path / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()

    dest_db = tmp_path / "feather_data.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("src", "erp.employees", "employees")],
    )

    _write_sql(
        tmp_path,
        "silver",
        "emp_clean",
        "SELECT id, name AS employee_name FROM bronze.src_employees",
    )
    _write_sql(
        tmp_path,
        "gold",
        "emp_snapshot",
        (
            "-- materialized: true\n"
            "SELECT * FROM silver.emp_clean"
        ),
    )

    cfg = load_config(config_file)
    results = run_all(cfg)

    assert any(r.status == "success" for r in results)

    con = duckdb.connect(str(dest_db))
    gold_rows = con.execute("SELECT * FROM gold.emp_snapshot").fetchall()
    assert len(gold_rows) == 2
    con.close()


def test_force_views_then_default_rematerializes_gold(tmp_path: Path):
    """Toggling force_views: first run with force_views=True creates VIEW,
    second run with default (force_views=False) promotes it to BASE TABLE."""
    source_db = tmp_path / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()

    dest_db = tmp_path / "feather_data.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("src", "erp.employees", "employees")],
    )

    _write_sql(
        tmp_path,
        "silver",
        "emp_clean",
        "SELECT id, name AS employee_name FROM bronze.src_employees",
    )
    _write_sql(
        tmp_path,
        "gold",
        "emp_snapshot",
        (
            "-- materialized: true\n"
            "SELECT * FROM silver.emp_clean"
        ),
    )

    # First run with force_views=True: gold stays a VIEW
    cfg = load_config(config_file)
    results1 = run_all(cfg, force_views=True)
    assert any(r.status == "success" for r in results1)

    con = duckdb.connect(str(dest_db))
    gold_type_force_views = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='emp_snapshot'"
    ).fetchone()[0]
    con.close()
    assert gold_type_force_views == "VIEW"

    # Second run with default (force_views=False): gold materializes as TABLE
    cfg2 = load_config(config_file)
    results2 = run_all(cfg2, force_views=False)
    assert all(r.status in ("success", "skipped") for r in results2)

    con = duckdb.connect(str(dest_db))
    gold_type_default = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='emp_snapshot'"
    ).fetchone()[0]
    con.close()
    assert gold_type_default == "BASE TABLE"


def test_run_no_rebuild_when_all_skipped(tmp_path: Path):
    source_db = tmp_path / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'Alice')")
    con.close()

    dest_db = tmp_path / "feather_data.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("src", "erp.employees", "employees")],
    )

    _write_sql(tmp_path, "gold", "emp_snap", ("-- materialized: true\nSELECT 1 AS val"))

    cfg = load_config(config_file)

    results1 = run_all(cfg)
    assert any(r.status == "success" for r in results1)

    results2 = run_all(cfg)
    assert all(r.status == "skipped" for r in results2)

    con = duckdb.connect(str(dest_db))
    row = con.execute("SELECT * FROM gold.emp_snap").fetchone()
    assert row is not None
    con.close()


def test_run_all_transform_rebuild_failure_is_caught_and_logged(
    tmp_path: Path, monkeypatch, caplog
):
    """Any exception raised inside the post-extraction transform rebuild
    block is swallowed and logged at error level — extraction results are
    still returned to the caller. (pipeline.py:589-590)"""
    import logging

    source_db = tmp_path / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()

    dest_db = tmp_path / "feather_data.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("src", "erp.employees", "employees")],
    )

    _write_sql(
        tmp_path,
        "silver",
        "emp_clean",
        "SELECT * FROM bronze.src_employees",
    )

    cfg = load_config(config_file)

    # Force discover_transforms to raise inside the post-extraction rebuild
    # block. pipeline.run_all imports it locally, so patch at its origin.
    from feather_etl import transforms as transforms_mod

    def _boom(_config_dir):
        raise RuntimeError("synthetic transform-discover failure")

    monkeypatch.setattr(transforms_mod, "discover_transforms", _boom)

    with caplog.at_level(logging.ERROR, logger="feather_etl.pipeline"):
        results = run_all(cfg)

    # Extract results are returned despite the failed transforms block.
    assert len(results) == 1
    assert results[0].status == "success"
    assert "Transform rebuild failed" in caplog.text
    assert "synthetic transform-discover failure" in caplog.text


def test_run_auto_infers_silver_to_gold_dep(tmp_path: Path):
    """A gold transform with no -- depends_on: header but a `FROM silver.X`
    clause is correctly ordered after silver.X by run_all. Regression for
    issue #54 (the headline feature)."""
    source_db = tmp_path / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()

    dest_db = tmp_path / "feather_data.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("src", "erp.employees", "employees")],
    )

    _write_sql(
        tmp_path,
        "silver",
        "emp_clean",
        "SELECT id, name AS employee_name FROM bronze.src_employees",
    )
    # gold: only -- materialized: true, NO -- depends_on:.
    # The dep must be inferred from `FROM silver.emp_clean`.
    _write_sql(
        tmp_path,
        "gold",
        "emp_snapshot",
        (
            "-- materialized: true\n"
            "SELECT * FROM silver.emp_clean"
        ),
    )

    cfg = load_config(config_file)
    results = run_all(cfg)

    # run_all returns extraction RunResults, not transform-level results;
    # success of the inferred-dep ordering is proven by the gold rows
    # query below succeeding (which only works if silver.emp_clean was
    # built before gold.emp_snapshot).
    assert any(r.status == "success" for r in results), (
        f"extraction failed: {results}"
    )

    con = duckdb.connect(str(dest_db))
    gold_rows = con.execute("SELECT * FROM gold.emp_snapshot").fetchall()
    assert len(gold_rows) == 2
    con.close()
