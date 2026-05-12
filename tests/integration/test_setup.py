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


def test_prod_mode_executes_only_gold_transforms(project):
    """In prod mode, run_setup only runs the gold-schema transforms up-front;
    silver is built later during extract. (setup.py:51-52)

    This asserts the *selection* behaviour: the results list contains gold
    entries and no silver entries. Whether each gold actually succeeds
    depends on its dependencies, which are not part of run_setup's contract
    in prod mode.
    """
    import duckdb

    config_path = _setup_project(project)
    _write_silver_transform(project)
    _write_gold_transform(project, materialized=True)
    cfg = load_config(config_path, mode_override="prod")

    # Pre-create silver.orders_clean so the gold transform can succeed.
    # (prod setup deliberately skips silver; the runtime extract step
    # normally handles it.)
    con = duckdb.connect(str(cfg.destination.path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute("CREATE TABLE bronze.orders (id INTEGER)")
    con.execute("CREATE VIEW silver.orders_clean AS SELECT * FROM bronze.orders")
    con.close()

    result = run_setup(cfg)

    assert result.transform_results is not None
    # Only gold transforms are executed in prod mode — no silver entries
    schemas = {t.schema for t in result.transform_results}
    assert schemas == {"gold"}
    assert all(t.status == "success" for t in result.transform_results)
