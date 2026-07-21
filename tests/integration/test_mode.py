"""Integration: behaviors previously coupled to mode — now independent controls.

Four concerns:
  1. Target schema defaults to bronze.* (explicit target_table: overrides)
  2. column_map always applies when set
  3. defaults.row_limit always applies when set
  4. force_views controls DDL kind; default materializes gold
"""

from __future__ import annotations

import warnings
from pathlib import Path

import duckdb
import yaml

from feather_flow.config import load_config
from feather_flow.pipeline import run_all
from feather_flow.transforms import run_transforms
from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


def _run_pipeline(
    tmp_path: Path,
    column_map: dict[str, str] | None = None,
    target_table: str | None = None,
    row_limit: int | None = None,
) -> tuple:
    import shutil

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)
    dest_path = tmp_path / "output.duckdb"
    config: dict = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
    }
    if row_limit is not None:
        config["defaults"] = {"row_limit": row_limit}
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(tmp_path, [make_curation_entry("erp", "erp.customers", "customers")])
    cfg = load_config(config_path)
    if column_map is not None:
        cfg.tables[0].column_map = column_map
    if target_table is not None:
        cfg.tables[0].target_table = target_table
    results = run_all(cfg)
    return cfg, results, dest_path


def _run_with_transforms(tmp_path: Path, force_views: bool = False) -> Path:
    import shutil

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)
    silver_dir = tmp_path / "transforms" / "silver"
    gold_dir = tmp_path / "transforms" / "gold"
    silver_dir.mkdir(parents=True)
    gold_dir.mkdir(parents=True)
    (silver_dir / "customers_clean.sql").write_text(
        "SELECT customer_id, name AS customer_name, city\nFROM bronze.erp_customers\n"
    )
    (gold_dir / "customer_summary.sql").write_text(
        "-- materialized: true\nSELECT city, COUNT(*) AS customer_count\n"
        "FROM silver.customers_clean\nGROUP BY city\n"
    )
    dest_path = tmp_path / "output.duckdb"
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
                "destination": {"path": str(dest_path)},
            },
            default_flow_style=False,
        )
    )
    write_curation(tmp_path, [make_curation_entry("erp", "erp.customers", "customers")])
    cfg = load_config(config_path)
    cfg.tables[0].target_table = "bronze.erp_customers"
    run_all(cfg, force_views=force_views)
    return dest_path


def test_default_extracts_to_bronze(tmp_path):
    """No target_table set: data lands in bronze.<name> by default."""
    _, results, dest_path = _run_pipeline(tmp_path)
    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[0]
    con.close()
    assert count > 0


def test_explicit_target_table_overrides_default(tmp_path):
    """Explicit target_table: silver.erp_customers lands in silver."""
    _, results, dest_path = _run_pipeline(tmp_path, target_table="silver.erp_customers")
    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM silver.erp_customers").fetchone()[0]
    con.close()
    assert count > 0


def test_column_map_always_applied(tmp_path):
    """column_map present: columns filtered and renamed regardless of other config."""
    _, results, dest_path = _run_pipeline(
        tmp_path,
        column_map={"customer_id": "cust_id", "name": "cust_name"},
        target_table="silver.erp_customers",
    )
    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    cols = [
        row[0]
        for row in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='silver' AND table_name='erp_customers' "
            "ORDER BY ordinal_position"
        ).fetchall()
    ]
    con.close()
    assert "cust_id" in cols
    assert "cust_name" in cols
    assert "customer_id" not in cols
    assert "email" not in cols


def test_row_limit_always_applied(tmp_path):
    """defaults.row_limit always limits extracted rows when set."""
    _, results, dest_path = _run_pipeline(tmp_path, row_limit=2)
    assert results[0].status == "success"
    assert results[0].rows_loaded <= 2
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[0]
    con.close()
    assert count <= 2


def test_gold_materialized_by_default(tmp_path):
    """Gold transform marked materialized:true becomes a TABLE by default."""
    dest_path = _run_with_transforms(tmp_path, force_views=False)
    con = duckdb.connect(str(dest_path))
    result = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='customer_summary'"
    ).fetchone()
    con.close()
    assert result is not None
    assert result[0] == "BASE TABLE"


def test_force_views_prevents_materialization(tmp_path):
    """force_views=True: gold transform stays a VIEW even when marked materialized."""
    dest_path = _run_with_transforms(tmp_path, force_views=True)
    con = duckdb.connect(str(dest_path))
    result = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='customer_summary'"
    ).fetchone()
    con.close()
    assert result is not None
    assert result[0] == "VIEW"


def test_yaml_mode_key_ignored_with_warning(tmp_path):
    """YAML with 'mode:' key still loads — ignored, warning emitted."""
    import shutil

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
                "destination": {"path": str(tmp_path / "output.duckdb")},
                "mode": "prod",
            },
            default_flow_style=False,
        )
    )
    write_curation(tmp_path, [make_curation_entry("erp", "erp.customers", "customers")])
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cfg = load_config(config_path)
    assert not hasattr(cfg, "mode")
    assert any("mode" in str(warning.message).lower() for warning in w)
