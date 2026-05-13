"""Integration: append strategy — pipeline.run_table + run_all dispatch to
DuckDBDestination.load_append.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import duckdb
import yaml

from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


def _make_config(
    tmp_path: Path,
    source_db: Path,
    strategy: str = "append",
    source_table: str = "erp.customers",
) -> Path:
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [
            make_curation_entry(
                "src",
                source_table,
                "customers",
                strategy=strategy,
                primary_key=["customer_id"],
            ),
        ],
    )
    return config_path


def test_pipeline_dispatches_to_load_append(tmp_path: Path):
    from feather_etl.config import load_config
    from feather_etl.pipeline import run_table

    source_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", source_db)
    config_path = _make_config(tmp_path, source_db)

    cfg = load_config(config_path)
    result = run_table(cfg, cfg.tables[0], tmp_path)

    assert result.status == "success"
    assert result.rows_loaded == 4

    con = duckdb.connect(str(tmp_path / "feather_data.duckdb"), read_only=True)
    count = con.execute("SELECT COUNT(*) FROM bronze.src_customers").fetchone()[0]
    con.close()
    assert count == 4


def test_append_second_run_appends_on_change(tmp_path: Path):
    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    source_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", source_db)
    config_path = _make_config(tmp_path, source_db)

    cfg = load_config(config_path)

    results1 = run_all(cfg)
    assert all(r.status == "success" for r in results1)

    time.sleep(0.05)
    con = duckdb.connect(str(source_db))
    con.execute(
        "INSERT INTO erp.customers VALUES (105, 'New Corp', 'new@corp.com', 'Pune', 15000.00, CURRENT_TIMESTAMP)"
    )
    con.close()

    cfg2 = load_config(config_path)
    results2 = run_all(cfg2)
    assert all(r.status == "success" for r in results2)

    dest_con = duckdb.connect(str(tmp_path / "feather_data.duckdb"), read_only=True)
    count = dest_con.execute("SELECT COUNT(*) FROM bronze.src_customers").fetchone()[0]
    dest_con.close()
    assert count == 9


def test_append_unchanged_source_skips(tmp_path: Path):
    from feather_etl.config import load_config
    from feather_etl.pipeline import run_all

    source_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", source_db)
    config_path = _make_config(tmp_path, source_db)

    cfg = load_config(config_path)
    run_all(cfg)

    cfg2 = load_config(config_path)
    results2 = run_all(cfg2)
    assert all(r.status == "skipped" for r in results2)

    dest_con = duckdb.connect(str(tmp_path / "feather_data.duckdb"), read_only=True)
    count = dest_con.execute("SELECT COUNT(*) FROM bronze.src_customers").fetchone()[0]
    dest_con.close()
    assert count == 4
