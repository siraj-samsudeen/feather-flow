"""Integration: feather_etl.setup.run_setup — orchestrates state init +
destination creation + transform execution.
"""

from __future__ import annotations

from feather_etl.config import load_config
from feather_etl.setup import run_setup


def _setup_project(project):
    """Build a minimal feather project. Returns config path."""
    project.copy_fixture("sample_erp.sqlite")
    (project.root / "sample_erp.sqlite").rename(project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "name": "erp", "path": "./source.sqlite"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("erp", "orders", "orders")])
    return project.config_path


def _write_silver_transform(project) -> None:
    """Write a tiny silver view definition. The loader wraps the SELECT
    body in CREATE OR REPLACE VIEW silver.orders_clean AS ... at execution
    time, so the file only contains the SELECT."""
    tdir = project.root / "transforms" / "silver"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "orders_clean.sql").write_text(
        "SELECT * FROM bronze.orders\n"
    )


def test_initializes_state_db_and_destination(project):
    cfg = load_config(_setup_project(project))

    result = run_setup(cfg)

    assert result.state_db_path.exists()
    assert result.destination_path == cfg.destination.path
    assert result.transform_results is None  # no transforms in this project


def test_returns_none_transforms_when_no_transforms_found(project):
    cfg = load_config(_setup_project(project))
    result = run_setup(cfg)

    assert result.transform_results is None


def test_executes_transforms_when_present(project):
    config_path = _setup_project(project)
    _write_silver_transform(project)
    cfg = load_config(config_path)

    result = run_setup(cfg)

    assert result.transform_results is not None
    assert len(result.transform_results) >= 1
    assert any(
        t.name == "orders_clean" and t.schema == "silver"
        for t in result.transform_results
    )


def _write_gold_transform(project, *, materialized: bool) -> None:
    """Gold SQL that depends on silver.orders_clean. When ``materialized``,
    prod mode rebuilds it as a TABLE; otherwise it stays a view."""
    tdir = project.root / "transforms" / "gold"
    tdir.mkdir(parents=True, exist_ok=True)
    header = "-- materialized: true\n" if materialized else ""
    (tdir / "orders_summary.sql").write_text(
        header + "SELECT COUNT(*) AS n FROM silver.orders_clean\n"
    )


def test_default_setup_materializes_gold_when_marked(project):
    """By default, run_setup runs all transforms; gold marked materialized:true
    becomes a TABLE. Both silver and gold are executed."""
    import duckdb

    config_path = _setup_project(project)
    _write_silver_transform(project)
    _write_gold_transform(project, materialized=True)
    cfg = load_config(config_path)

    # Pre-seed bronze so silver and gold transforms can succeed.
    con = duckdb.connect(str(cfg.destination.path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("CREATE TABLE bronze.orders (id INTEGER, product VARCHAR)")
    con.close()

    result = run_setup(cfg)

    assert result.transform_results is not None
    schemas = {t.schema for t in result.transform_results}
    # Both silver and gold are executed
    assert "silver" in schemas
    assert "gold" in schemas
    gold_results = [t for t in result.transform_results if t.schema == "gold" and t.name == "orders_summary"]
    assert gold_results, "expected a gold orders_summary result"
    # Materialized gold ends up as a TABLE (the last result for that name reflects final state)
    gold_types = {t.type for t in gold_results}
    assert "table" in gold_types
