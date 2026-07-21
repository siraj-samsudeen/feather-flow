"""Integration: data quality checks — pipeline.run_table with dq_checks
applied to bronze tables.
"""

from __future__ import annotations

from pathlib import Path


def test_dq_results_stored_in_state(tmp_path: Path):
    import shutil
    import yaml

    from tests.conftest import FIXTURES_DIR
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table
    from feather_flow.state import StateManager
    from tests.helpers import make_curation_entry, write_curation

    client_db = tmp_path / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", client_db)
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(client_db)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [
            make_curation_entry(
                "src",
                "erp.customers",
                "customers",
                primary_key=["customer_id"],
            ),
        ],
    )
    cfg = load_config(tmp_path / "feather.yaml")
    cfg.tables[0].quality_checks = {"not_null": ["name"]}

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"

    sm = StateManager(tmp_path / "feather_state.duckdb")
    con = sm._connect()
    rows = con.execute(
        "SELECT * FROM _dq_results WHERE table_name = 'src_customers'"
    ).fetchall()
    con.close()
    assert len(rows) >= 2


def test_dq_failure_does_not_block_pipeline(tmp_path: Path):
    import shutil
    import yaml

    from tests.conftest import FIXTURES_DIR
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table
    from tests.helpers import make_curation_entry, write_curation

    client_db = tmp_path / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", client_db)
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(client_db)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [
            make_curation_entry(
                "src",
                "erp.customers",
                "customers",
                primary_key=["customer_id"],
            ),
        ],
    )
    cfg = load_config(tmp_path / "feather.yaml")
    cfg.tables[0].quality_checks = {"unique": ["name"]}

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"
