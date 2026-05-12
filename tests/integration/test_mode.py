"""Integration: mode flag semantics — pipeline.run_all behavior across
dev/prod/test modes, row_limit, transforms.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


# --- Shared helpers ---


def _run_pipeline(
    tmp_path: Path,
    mode: str,
    column_map: dict[str, str] | None = None,
    target_table: str | None = None,
) -> tuple:
    """Helper: run pipeline and return (config, results, dest_path)."""
    import shutil

    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    dest_path = tmp_path / "output.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
        "mode": mode,
    }
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)

    # Apply overrides that curation doesn't handle
    if column_map is not None:
        cfg.tables[0].column_map = column_map
    if target_table is not None:
        cfg.tables[0].target_table = target_table

    results = run_all(cfg, config_path)
    return cfg, results, dest_path


def _run_with_transforms(tmp_path: Path, mode: str) -> Path:
    """Helper: run pipeline with a gold transform SQL file and return dest_path."""
    import shutil

    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    silver_dir = tmp_path / "transforms" / "silver"
    gold_dir = tmp_path / "transforms" / "gold"
    silver_dir.mkdir(parents=True)
    gold_dir.mkdir(parents=True)

    (silver_dir / "customers_clean.sql").write_text(
        "SELECT customer_id, name AS customer_name, city\n"
        "FROM bronze.erp_customers\n"
    )

    (gold_dir / "customer_summary.sql").write_text(
        "-- materialized: true\n"
        "SELECT city, COUNT(*) AS customer_count\n"
        "FROM silver.customers_clean\n"
        "GROUP BY city\n"
    )

    dest_path = tmp_path / "output.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
        "mode": mode,
    }
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)
    # Force target to bronze so transforms can reference it consistently
    cfg.tables[0].target_table = "bronze.erp_customers"
    run_all(cfg, config_path)
    return dest_path


# --- Pipeline mode-driven target + column_map ---


def test_dev_extracts_to_bronze(tmp_path):
    """Dev mode: data lands in bronze.{name}."""
    _, results, dest_path = _run_pipeline(tmp_path, mode="dev")

    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[0]
    con.close()
    assert count > 0


def test_prod_extracts_to_silver(tmp_path):
    """Prod mode without column_map: all columns land in silver.{name}."""
    _, results, dest_path = _run_pipeline(tmp_path, mode="prod")

    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM silver.erp_customers").fetchone()[0]
    con.close()
    assert count > 0


def test_prod_with_column_map(tmp_path):
    """Prod mode with column_map: only mapped columns, renamed, in silver."""
    _, results, dest_path = _run_pipeline(
        tmp_path,
        mode="prod",
        column_map={"customer_id": "cust_id", "name": "cust_name"},
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


def test_dev_with_column_map_ignores_it(tmp_path):
    """Dev mode ignores column_map -- extracts all columns to bronze."""
    _, results, dest_path = _run_pipeline(
        tmp_path,
        mode="dev",
        column_map={"customer_id": "cust_id", "name": "cust_name"},
    )

    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    cols = [
        row[0]
        for row in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='bronze' AND table_name='erp_customers' "
            "ORDER BY ordinal_position"
        ).fetchall()
    ]
    con.close()

    assert "customer_id" in cols
    assert "name" in cols
    assert "email" in cols


def test_explicit_target_overrides_mode(tmp_path):
    """Explicit target_table overrides mode-derived target."""
    _, results, dest_path = _run_pipeline(
        tmp_path,
        mode="prod",
        target_table="bronze.erp_customers",
    )

    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[0]
    con.close()
    assert count > 0


# --- Gold materialization per mode ---


def test_prod_gold_materialized(tmp_path):
    """Prod mode: gold transform is a materialized TABLE."""
    dest_path = _run_with_transforms(tmp_path, mode="prod")

    con = duckdb.connect(str(dest_path))
    result = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='customer_summary'"
    ).fetchone()
    con.close()

    assert result is not None
    assert result[0] == "BASE TABLE"


def test_dev_gold_is_view(tmp_path):
    """Dev mode: gold transform is a VIEW, not a materialized table."""
    dest_path = _run_with_transforms(tmp_path, mode="dev")

    con = duckdb.connect(str(dest_path))
    result = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='customer_summary'"
    ).fetchone()
    con.close()

    assert result is not None
    assert result[0] == "VIEW"


# --- row_limit for test mode ---


def test_test_mode_with_row_limit(tmp_path):
    """Test mode with row_limit: extracts at most N rows."""
    import shutil

    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    dest_path = tmp_path / "output.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
        "mode": "test",
        "defaults": {"row_limit": 2},
    }
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)
    results = run_all(cfg, config_path)

    assert results[0].status == "success"
    assert results[0].rows_loaded <= 2

    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[0]
    con.close()
    assert count <= 2


def test_dev_mode_ignores_row_limit(tmp_path):
    """Dev mode ignores row_limit even if set."""
    import shutil

    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    dest_path = tmp_path / "output.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
        "mode": "dev",
        "defaults": {"row_limit": 2},
    }
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)
    results = run_all(cfg, config_path)

    assert results[0].status == "success"
    assert results[0].rows_loaded > 2


# --- CLI + env var mode override E2E ---


def test_cli_mode_overrides_yaml(tmp_path):
    """--mode prod CLI flag overrides mode: dev in YAML."""
    _, results, dest_path = _run_pipeline(tmp_path, mode="dev")
    con = duckdb.connect(str(dest_path))
    bronze_count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[
        0
    ]
    con.close()
    assert bronze_count > 0

    import shutil

    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    tmp2 = tmp_path / "prod_run"
    tmp2.mkdir()
    src_db = tmp2 / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    dest_path2 = tmp2 / "output.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path2)},
        "mode": "dev",
    }
    config_path = tmp2 / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp2,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path, mode_override="prod")
    assert cfg.mode == "prod"
    run_all(cfg, config_path)

    con = duckdb.connect(str(dest_path2))
    silver_count = con.execute("SELECT COUNT(*) FROM silver.erp_customers").fetchone()[
        0
    ]
    con.close()
    assert silver_count > 0


def test_env_var_overrides_yaml(tmp_path, monkeypatch):
    """FEATHER_MODE=prod env var overrides mode: dev in YAML."""
    import shutil

    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    dest_path = tmp_path / "output.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
        "mode": "dev",
    }
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    monkeypatch.setenv("FEATHER_MODE", "prod")
    cfg = load_config(config_path)
    assert cfg.mode == "prod"
    run_all(cfg, config_path)

    con = duckdb.connect(str(dest_path))
    silver_count = con.execute("SELECT COUNT(*) FROM silver.erp_customers").fetchone()[
        0
    ]
    con.close()
    assert silver_count > 0
