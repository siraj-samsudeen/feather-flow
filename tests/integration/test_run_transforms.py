"""Direct unit tests for transforms.run_transforms() — the shared helper that
exposes the post-extract transform block as a standalone callable.

These tests call run_transforms() directly (not via `feather run` or
pipeline.run_all). They pin the helper's contract independent of the CLI.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from feather_etl.config import load_config
from feather_etl.transforms import TransformResult, run_transforms
from tests.helpers import make_curation_entry, write_curation


def _write_sql(base: Path, schema: str, name: str, content: str) -> Path:
    """Write a .sql file under base/transforms/{schema}/{name}.sql."""
    d = base / "transforms" / schema
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.sql"
    p.write_text(content)
    return p


def _prepare_dest_with_bronze(dest_db: Path) -> None:
    """Pre-populate destination with the standard schemas + a bronze table."""
    con = duckdb.connect(str(dest_db))
    for schema in ("bronze", "silver", "gold", "_quarantine"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute("CREATE TABLE bronze.src_employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO bronze.src_employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()


def _write_config(
    tmp_path: Path, dest_db: Path, mode: str
) -> Path:
    """Minimal config file with a single source (unused by run_transforms)."""
    source_db = tmp_path / "source.duckdb"
    # Touch a source file so config validation passes; run_transforms never
    # opens it.
    duckdb.connect(str(source_db)).close()
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
        "mode": mode,
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("src", "erp.employees", "employees")],
    )
    return config_file


def _write_two_transforms(tmp_path: Path) -> None:
    """One silver view + one materialized gold."""
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
            "-- depends_on: silver.emp_clean\n"
            "-- materialized: true\n"
            "SELECT * FROM silver.emp_clean"
        ),
    )


def test_run_transforms_returns_list_of_transform_result(tmp_path: Path):
    """Helper returns list[TransformResult]; contents reflect what was executed."""
    dest_db = tmp_path / "feather_data.duckdb"
    _prepare_dest_with_bronze(dest_db)
    _write_two_transforms(tmp_path)
    config_file = _write_config(tmp_path, dest_db, mode="dev")

    cfg = load_config(config_file)
    results = run_transforms(cfg)

    assert isinstance(results, list)
    assert all(isinstance(r, TransformResult) for r in results)
    # Dev: one result per transform (no rebuild pass).
    assert len(results) == 2
    by_name = {(r.schema, r.name): r for r in results}
    assert ("silver", "emp_clean") in by_name
    assert ("gold", "emp_snapshot") in by_name
    for r in results:
        assert r.status == "success", f"{r.schema}.{r.name}: {r.error}"


def test_run_transforms_dev_mode_creates_views(tmp_path: Path):
    """Dev mode: gold + silver both land as VIEWs in the destination."""
    dest_db = tmp_path / "feather_data.duckdb"
    _prepare_dest_with_bronze(dest_db)
    _write_two_transforms(tmp_path)
    config_file = _write_config(tmp_path, dest_db, mode="dev")

    cfg = load_config(config_file)
    run_transforms(cfg)

    con = duckdb.connect(str(dest_db))
    silver_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='silver' AND table_name='emp_clean'"
    ).fetchone()
    gold_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='emp_snapshot'"
    ).fetchone()
    con.close()

    assert silver_type == ("VIEW",)
    assert gold_type == ("VIEW",)


def test_run_transforms_prod_mode_materializes_gold(tmp_path: Path):
    """Prod mode: silver stays a VIEW; materialized gold becomes a TABLE."""
    dest_db = tmp_path / "feather_data.duckdb"
    _prepare_dest_with_bronze(dest_db)
    _write_two_transforms(tmp_path)
    config_file = _write_config(tmp_path, dest_db, mode="prod")

    cfg = load_config(config_file)
    results = run_transforms(cfg)

    # Prod path returns execute_transforms + rebuild_materialized_gold results.
    # The single materialized gold transform contributes once from each pass:
    # 2 transforms (initial) + 1 gold rebuild = 3 results.
    assert len(results) == 3
    assert all(r.status == "success" for r in results)

    con = duckdb.connect(str(dest_db))
    silver_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='silver' AND table_name='emp_clean'"
    ).fetchone()
    gold_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='emp_snapshot'"
    ).fetchone()
    con.close()

    assert silver_type == ("VIEW",)
    assert gold_type == ("BASE TABLE",)


def test_run_transforms_no_transforms_returns_empty_list(tmp_path: Path):
    """No transforms directory: helper returns []."""
    dest_db = tmp_path / "feather_data.duckdb"
    duckdb.connect(str(dest_db)).close()
    config_file = _write_config(tmp_path, dest_db, mode="dev")

    cfg = load_config(config_file)
    results = run_transforms(cfg)

    assert results == []
